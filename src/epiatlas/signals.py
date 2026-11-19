"""Step 2: read EEG / sEEG / MEG recordings, re-reference them (montage) and filter them."""
import re
from functools import lru_cache

import mne
import numpy as np
from scipy.signal import butter, filtfilt, iirnotch, sosfiltfilt

from .ingest import SCALP_10_20, clean_channel

# Channels that are not brain signals. Hospital EDF files often label them all as "EEG",
# so we also recognise them by name.
NOT_BRAIN_TYPES = {"stim", "misc", "ecg", "emg", "eog", "resp", "bio", "syst"}
NOT_BRAIN_NAMES = r"^(ECG|EKG|EMG|EOG|RESP|PULSE|SPO2|PHOTIC|DC\d+|MARK\d*|EVENT|TRIG)"


@lru_cache(maxsize=16)  # keep recently used files open, so scrolling through a recording is fast
def open_recording(path):
    """The recording (data stays on disk until needed) and the positions of its brain channels."""
    raw = mne.io.read_raw(path, preload=False, verbose="error")
    brain_channels = []
    for i, (name, kind) in enumerate(zip(raw.ch_names, raw.get_channel_types())):
        if kind not in NOT_BRAIN_TYPES and not re.match(NOT_BRAIN_NAMES, clean_channel(name)):
            brain_channels.append(i)
    return raw, brain_channels


def bipolar_pairs(names):
    """sEEG neighbour pairs on the same electrode: A1-A2, A2-A3, ...

    Scalp electrodes sometimes recorded in the same file (F3, F4, C3, C4 ...) are left out:
    'C3-C4' would wrongly subtract the left side of the head from the right side.
    """
    electrodes = {}  # 'A' -> {1: 'A1', 2: 'A2', ...}
    for name in names:
        match = re.match(r"^(.*?)(\d+)$", name.strip())
        if match:
            electrodes.setdefault(match.group(1), {})[int(match.group(2))] = name

    pairs = []
    for contacts in electrodes.values():
        if all(clean_channel(name) in SCALP_10_20 for name in contacts.values()):
            continue  # only scalp names: not an sEEG electrode
        for number in sorted(contacts):
            if number + 1 in contacts:
                pairs.append((contacts[number], contacts[number + 1]))
    return pairs


def apply_montage(signals, names, montage):
    """'average': subtract the mean of all channels.  'bipolar': neighbour minus neighbour.  'raw': unchanged."""
    if montage == "average":
        return signals - signals.mean(axis=0), names
    if montage == "bipolar":
        pairs = bipolar_pairs(names)
        if pairs:
            row = {name: i for i, name in enumerate(names)}
            new_signals = np.array([signals[row[a]] - signals[row[b]] for a, b in pairs])
            new_names = [f"{a}-{b}" for a, b in pairs]
            return new_signals, new_names
    return signals, names


def filter_signal(signals, sfreq, low=None, high=None, notch=None):
    """Keep only frequencies between low and high (Hz), and remove one 'notch' frequency (mains noise)."""
    if notch and notch < sfreq / 2:
        b, a = iirnotch(notch, 30, sfreq)
        signals = filtfilt(b, a, signals, axis=-1)
    if high:
        high = min(high, 0.45 * sfreq)  # a filter cannot go above half the sampling rate
    if low and high:
        sos = butter(4, [low, high], "bandpass", fs=sfreq, output="sos")
    elif low:
        sos = butter(4, low, "highpass", fs=sfreq, output="sos")
    elif high:
        sos = butter(4, high, "lowpass", fs=sfreq, output="sos")
    else:
        return signals
    return sosfiltfilt(sos, signals, axis=-1)  # forwards and backwards, so nothing is shifted in time


def read_window(path, start, duration, montage="raw", low=None, high=None, notch=None, max_points=4000):
    """A piece of the recording for the screen, in microvolts."""
    raw, picks = open_recording(path)
    sfreq = raw.info["sfreq"]

    # Read 1 extra second on each side: filters distort the edges, and the extra is cut off afterwards.
    first = max(0, int((start - 1) * sfreq))
    last = min(raw.n_times, int((start + duration + 1) * sfreq))
    signals = raw.get_data(picks=picks, start=first, stop=last) * 1e6  # volts -> microvolts
    names = [raw.ch_names[i] for i in picks]

    signals, names = apply_montage(signals, names, montage)
    signals = filter_signal(signals, sfreq, low, high, notch)
    skip = int(start * sfreq) - first
    signals = signals[:, skip:skip + int(duration * sfreq)]

    # A screen is ~2000 pixels wide, so send at most ~4000 points per channel (display only).
    step = max(1, signals.shape[1] // max_points)
    return {"channels": names, "sfreq": sfreq / step, "start": start,
            "data": np.round(signals[:, ::step], 2).tolist()}


def info(path):
    raw, picks = open_recording(path)
    annotations = []
    for a in raw.annotations:
        annotations.append({"onset": float(a["onset"]), "duration": float(a["duration"]), "text": a["description"]})
    return {"channels": [raw.ch_names[i] for i in picks], "sfreq": raw.info["sfreq"],
            "duration": raw.times[-1], "annotations": annotations}
