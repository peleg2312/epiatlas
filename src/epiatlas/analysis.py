"""Step 3: the "machine". A blind reading of the raw signal.

It never reads the doctors' annotations, reports or onset channels. Their answer is
fetched only afterwards, to measure agreement (see concordance below).

Three published, fixed methods:
  seizure in sEEG          -> Epileptogenicity Index (Bartolomei et al., Brain 2008)
  seizure in scalp EEG/MEG -> line length (Esteller et al., 2001)
  between seizures         -> number of spikes per minute on each channel
"""
import numpy as np
from scipy.signal import find_peaks, spectrogram

from .ingest import clean_channel
from .signals import apply_montage, bipolar_pairs, filter_signal, open_recording

BANDS = {"theta": (3.5, 7.4), "alpha": (7.4, 12.4), "beta": (12.4, 24), "gamma": (24, 97)}

# The settings ("calibration knobs"). Defaults follow the papers. Tune them once on patients
# set aside for tuning, then freeze them: the same settings must be used for every patient.
SETTINGS = {
    "line_freq": 50.0,          # mains electricity in Israel is 50 Hz
    "ei_bias": 0.5,             # Page-Hinkley: how much ER must rise to count
    "ei_threshold": 15.0,       # Page-Hinkley: how long it must keep rising
    "ei_tau_s": 1.0,            # EI: a 1 s grace period for later channels
    "ei_window_s": 5.0,         # EI: seconds of ER summed after a channel's onset
    "ll_ratio": 2.0,            # line length: 'seizure' = 2x the channel's usual value
    "spike_band": (10.0, 45.0), # spikes are sharp, so look at 10-45 Hz
    "spike_z": 6.0,             # a spike must stand 6 'robust standard deviations' above normal
    "spike_refractory_s": 0.25, # count at most one spike per 0.25 s
    "max_minutes": 10.0,        # analyse the first 10 minutes of a file
}


def ranked(names, scores, onsets):
    """Rows sorted from the strongest channel down, with scores scaled so the strongest is 1."""
    top = max(scores) or 1.0
    rows = []
    for name, score, onset in zip(names, scores, onsets):
        rows.append({"channel": name, "score": round(float(score / top), 3),
                     "onset_s": None if onset is None else round(float(onset), 2)})
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows


# ---------- Seizures in sEEG: Epileptogenicity Index ----------
def energy_ratio(signals, sfreq):
    """Every 0.25 s, per channel: fast energy (beta + gamma) divided by slow energy (theta + alpha).
    At the start of an sEEG seizure fast activity appears, so this ratio jumps up."""
    window = int(1.0 * sfreq)
    step = int(0.25 * sfreq)
    freqs, times, power = spectrogram(signals, fs=sfreq, nperseg=window, noverlap=window - step, axis=-1)

    def band_energy(band):
        low, high = BANDS[band]
        in_band = (freqs >= low) & (freqs < high)
        return power[:, in_band, :].sum(axis=1)

    fast = band_energy("beta") + band_energy("gamma")
    slow = band_energy("theta") + band_energy("alpha")
    return times, fast / (slow + 1e-20)


def page_hinkley(ratio, bias, threshold):
    """Page-Hinkley change detection: the time step where the ratio starts rising for good, or None."""
    ratio = ratio / np.median(ratio)  # relative to the channel's own usual value
    running_mean = np.cumsum(ratio) / np.arange(1, len(ratio) + 1)
    total = np.cumsum(ratio - running_mean - bias)
    lowest_so_far = np.minimum.accumulate(total)
    alarms = np.flatnonzero(total - lowest_so_far > threshold)
    if len(alarms) == 0:
        return None
    alarm = alarms[0]
    # The change started at the last low point before the alarm
    return int(np.flatnonzero(total[:alarm + 1] == lowest_so_far[alarm])[-1])


def epileptogenicity(signals, sfreq, names, s=SETTINGS):
    """EI = (how much fast activity) / (how late). Early, strong channels score high."""
    times, ratio = energy_ratio(signals, sfreq)
    step = times[1] - times[0]
    detections = [page_hinkley(ratio[i], s["ei_bias"], s["ei_threshold"]) for i in range(len(names))]
    found = [d for d in detections if d is not None]
    if not found:
        return ranked(names, [0.0] * len(names), [None] * len(names))

    earliest = min(found)
    window = int(s["ei_window_s"] / step)
    scores, onsets = [], []
    for i, d in enumerate(detections):
        if d is None:
            scores.append(0.0)
            onsets.append(None)
        else:
            delay = times[d] - times[earliest]  # seconds after the first channel
            scores.append(ratio[i, d:d + window].sum() / (delay + s["ei_tau_s"]))
            onsets.append(times[d])
    return ranked(names, scores, onsets)


# ---------- Seizures in scalp EEG / MEG: line length ----------
def line_length_onset(signals, sfreq, names, s=SETTINGS):
    """'Line length' = how much the signal moves up and down each second (Esteller 2001).
    A seizure makes it rise. Needs some normal recording before the seizure as the baseline."""
    signals = filter_signal(signals, sfreq, high=30.0)  # scalp seizure rhythms are below 30 Hz; above is muscle
    second = int(sfreq)
    n_seconds = (signals.shape[1] - 1) // second
    jumps = np.abs(np.diff(signals, axis=1))[:, :n_seconds * second]
    per_second = jumps.reshape(len(names), n_seconds, second).sum(axis=2)
    ratio = per_second / (np.median(per_second, axis=1, keepdims=True) + 1e-12)

    high = ratio > s["ll_ratio"]
    three_in_a_row = high[:, :-2] & high[:, 1:-1] & high[:, 2:]
    detections = [int(np.argmax(row)) if row.any() else None for row in three_in_a_row]
    found = [d for d in detections if d is not None]

    scores = []
    for i, d in enumerate(detections):
        if d is None:
            scores.append(0.0)
        else:
            scores.append(ratio[i, d:d + 10].mean() / (d - min(found) + s["ei_tau_s"]))
    return ranked(names, scores, detections)  # onset = the second where it started


# ---------- Between seizures: spikes ----------
def spike_rates(signals, sfreq, names, s=SETTINGS):
    """Count sharp peaks that stand far above each channel's normal level."""
    size = np.abs(filter_signal(signals, sfreq, *s["spike_band"]))
    median = np.median(size, axis=1, keepdims=True)
    spread = 1.4826 * np.median(np.abs(size - median), axis=1, keepdims=True)  # a 'robust' standard deviation
    z = (size - median) / (spread + 1e-12)
    minutes = signals.shape[1] / sfreq / 60

    rows = []
    for name, channel_z in zip(names, z):
        peaks, _ = find_peaks(channel_z, height=s["spike_z"], distance=int(s["spike_refractory_s"] * sfreq))
        rows.append({"channel": name, "spikes": len(peaks), "per_min": round(len(peaks) / minutes, 2),
                     "times_s": np.round(peaks[:300] / sfreq, 2).tolist()})
    rows.sort(key=lambda row: row["per_min"], reverse=True)
    return rows


# ---------- Run the right method on a recording ----------
def analyse(path, modality, state, start_s=0.0, s=SETTINGS):
    raw, picks = open_recording(path)
    sfreq = raw.info["sfreq"]
    first = int(start_s * sfreq)
    last = min(raw.n_times, first + int(s["max_minutes"] * 60 * sfreq))
    # Limitation: one window of max_minutes. Long monitoring files need a chosen seizure window.
    signals = raw.get_data(picks=picks, start=first, stop=last) * 1e6
    names = [raw.ch_names[i] for i in picks]

    # sEEG is read in bipolar montage (the standard); scalp EEG and MEG against the average
    if modality == "seeg" and bipolar_pairs(names):
        montage = "bipolar"
    else:
        montage = "average"
    signals, names = apply_montage(signals, names, montage)
    signals = filter_signal(signals, sfreq, notch=s["line_freq"])
    signals = filter_signal(signals, sfreq, notch=2 * s["line_freq"])
    signals = filter_signal(signals, sfreq, low=1.0)  # remove slow drift

    offset = first / sfreq  # times are reported from the start of the file
    result = {"montage": montage, "window_s": [offset, last / sfreq], "state": state, "settings": s}

    if state != "interictal":  # a seizure, or unknown: look for a seizure
        if modality == "seeg":
            result["ictal_method"] = "Epileptogenicity Index"
            result["ictal"] = epileptogenicity(signals, sfreq, names, s)
        else:
            result["ictal_method"] = "Line-length onset"
            result["ictal"] = line_length_onset(signals, sfreq, names, s)
        for row in result["ictal"]:
            if row["onset_s"] is not None:
                row["onset_s"] = round(row["onset_s"] + offset, 2)

    if state != "ictal":  # between seizures, or unknown: count spikes
        result["interictal"] = spike_rates(signals, sfreq, names, s)
        for row in result["interictal"]:
            row["times_s"] = [round(t + offset, 2) for t in row["times_s"]]
    return result


# ---------- Compare with the doctors (only after "Show doctors' reading") ----------
def concordance(ranking, doctors_channels):
    """Which of the doctors' contacts did the machine also flag?

    The machine flags its top k channels with a score above 0, where k = the number of
    doctors' contacts. A bipolar channel like A1-A2 counts for both A1 and A2.
    """
    doctors = {clean_channel(c) for c in doctors_channels}
    score_name = "score" if ranking and "score" in ranking[0] else "per_min"
    top = [row["channel"] for row in ranking[:max(len(doctors), 1)] if row[score_name] > 0]

    machine = set()
    for channel in top:
        for contact in clean_channel(channel).split("-"):  # clean first: 'EEG T7-Ref' -> 'T7'
            if contact:
                machine.add(contact)

    both = machine & doctors
    return {"clinical": sorted(doctors), "machine_top": top, "machine_contacts": sorted(machine),
            "found": sorted(both), "extra": sorted(machine - doctors),
            "agreement": round(len(both) / len(doctors), 2) if doctors else 0.0}
