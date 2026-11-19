"""Generate a SYNTHETIC demo patient (DEMO001) so the system can be shown without real data.

Story: left mesial temporal lobe epilepsy. Seizures start in sEEG electrode A (left hippocampus,
contacts A1-A3), spread to B (left lateral temporal) ~5 s later; C (right temporal) stays normal.
Interictal spikes are frequent on A2/A3, rare on C4. PET shows left temporal hypometabolism.
Nothing here is real patient data.

Usage: python scripts/make_demo_data.py [data_dir]
"""
import csv
import json
import sys
from pathlib import Path

import mne
import nibabel as nib
import numpy as np
from scipy.signal import butter, sosfiltfilt

rng = np.random.default_rng(7)
LINE = 50.0  # Israeli mains, added on purpose so the analysis has to remove it


def pink(n_ch, n, sf, uv):
    f = np.fft.rfftfreq(n, 1 / sf)
    spec = rng.standard_normal((n_ch, f.size)) + 1j * rng.standard_normal((n_ch, f.size))
    x = np.fft.irfft(spec / np.sqrt(np.maximum(f, 1.0)), n)
    return x / x.std(1, keepdims=True) * uv


def ramp(t, t0, t1):
    return np.clip((t - t0) / (t1 - t0), 0, 1)


def seizure(t, onset, sf, scale=1.0, lvfa=True):
    """Low-voltage fast activity (LVFA) for 3 s, then growing rhythmic ~6 Hz discharges until onset+25 s."""
    on = (t >= onset) & (t < onset + 25)
    fast = np.zeros_like(t)
    if lvfa:
        fast = sosfiltfilt(butter(4, [25, 90], "bandpass", fs=sf, output="sos"), pink(1, t.size, sf, 1)[0])
        fast = fast / fast.std() * 45 * ((t >= onset) & (t < onset + 3))
    freq = 7 - 3 * ramp(t, onset, onset + 25)  # slows down as the seizure evolves
    rhythm = np.sin(2 * np.pi * np.cumsum(freq) / sf) ** 9 * 300 * ramp(t, onset + 2, onset + 12) * on
    return scale * (fast + rhythm)


def spike(sf):
    tt = np.arange(int(0.35 * sf)) / sf
    return (-250 * np.exp(-((tt - 0.03) / 0.012) ** 2) + 120 * np.exp(-((tt - 0.06) / 0.02) ** 2)
            + 60 * np.exp(-((tt - 0.18) / 0.06) ** 2))


def write_edf(path, x_uv, names, sf, ch_type, notes):
    info = mne.create_info(names, sf, ch_type)
    raw = mne.io.RawArray(x_uv * 1e-6, info, verbose="error")
    raw.set_annotations(mne.Annotations(*zip(*notes)) if notes else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    mne.export.export_raw(path, raw, fmt="edf", overwrite=True, verbose="error")


def seeg_names():
    return [f"{e}{i}" for e in "ABC" for i in range(1, 7)]


def make_seeg_ictal(out):
    sf, dur = 512, 70
    t = np.arange(int(sf * dur)) / sf
    names = seeg_names()
    x = pink(len(names), t.size, sf, 35)
    # (onset s, amplitude, starts with fast activity?) Neighbours differ, so bipolar montages keep the seizure.
    involved = {"A1": (30.0, 1.0, True), "A2": (30.0, 0.7, True), "A3": (30.0, 0.4, True),
                "B1": (35.5, 0.6, False), "B2": (35.5, 0.35, False), "B3": (40.0, 0.2, False)}
    for i, n in enumerate(names):
        if n in involved:
            onset, amp, lvfa = involved[n]
            x[i] += seizure(t, onset, sf, amp, lvfa)
    x += 12 * np.sin(2 * np.pi * LINE * t)
    notes = [(31.0, 0, "EEG onset (clinician)"), (34.0, 0, "Semiology: oral automatisms, R hand dystonia"),
             (55.5, 0, "End of seizure (clinician)")]
    write_edf(out / "seizures" / "DEMO001_sz1_seeg.edf", x, names, sf, "seeg", notes)


def make_seeg_interictal(out):
    sf, dur = 512, 180
    t = np.arange(int(sf * dur)) / sf
    names = seeg_names()
    x = pink(len(names), t.size, sf, 35)
    s = spike(sf)
    for times, chans in ((rng.uniform(1, dur - 1, 36), {"A2": 1.0, "A3": 0.9, "A1": 0.5}),
                         (rng.uniform(1, dur - 1, 6), {"C4": 0.8})):
        for t0 in times:
            k = int(t0 * sf)
            for n, g in chans.items():
                x[names.index(n), k:k + s.size] += g * s
    x += 12 * np.sin(2 * np.pi * LINE * t)
    write_edf(out / "interictal" / "DEMO001_interictal_seeg.edf", x, names, sf, "seeg", [])


def make_scalp_ictal(out):
    sf, dur = 256, 70
    t = np.arange(int(sf * dur)) / sf
    base = ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "T7", "C3", "Cz", "C4", "T8",
            "P7", "P3", "Pz", "P4", "P8", "O1", "O2"]
    x = pink(len(base), t.size, sf, 20)
    for i, n in enumerate(base):
        if n in ("O1", "O2", "P3", "P4", "Pz"):
            x[i] += 25 * np.sin(2 * np.pi * 10 * t) * (t < 32)  # posterior alpha before the seizure
        if n in ("F7", "T7", "P7"):
            x[i] += 0.3 * seizure(t, 33.0, sf, lvfa=False)
    x += 8 * np.sin(2 * np.pi * LINE * t)
    names = [f"EEG {n}-Ref" for n in base]  # how clinical EDF exports often name channels
    write_edf(out / "seizures" / "DEMO001_sz1_scalp.edf", x, names, sf, "eeg",
              [(33.5, 0, "Scalp onset L temporal (clinician)")])


CONTACTS = {  # world mm (RAS); deepest contact first, 5 mm spacing
    "A": ((-28, -20, -15), (-1, 0, 0)), "B": ((-38, 5, -22), (-1, 0, 0)), "C": ((28, -20, -15), (1, 0, 0))}


def make_volumes(out):
    vox = 2.0
    shape = (90, 108, 90)
    aff = np.diag([vox, vox, vox, 1.0])
    aff[:3, 3] = -np.array(shape) * vox / 2
    ijk = np.indices(shape).reshape(3, -1)
    xyz = (aff @ np.vstack([ijk, np.ones(ijk.shape[1])]))[:3].reshape(3, *shape)
    r = np.sqrt((xyz[0] / 68) ** 2 + (xyz[1] / 85) ** 2 + ((xyz[2] + 5) / 60) ** 2)
    blob = lambda c, rad: np.exp(-(((xyz - np.array(c)[:, None, None, None]) ** 2).sum(0)) / (2 * rad ** 2))

    t1 = np.select([r < 0.75, r < 0.95, r < 1.0, r < 1.08, r < 1.15], [140, 90, 30, 60, 120], 0).astype(np.float32)
    t1 -= 35 * blob((-28, -20, -15), 4)  # subtle left hippocampal signal change
    t1 += rng.normal(0, 4, shape) * (r < 1.15)
    (out / "imaging").mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(t1.astype(np.float32), aff), out / "imaging" / "DEMO001_T1_mprage.nii.gz")

    ct = np.select([r < 1.0, r < 1.08], [40, 1200], -1000).astype(np.float32)
    for (x0, d) in CONTACTS.values():
        for k in range(6):
            ct += 3000 * blob(np.array(x0) + 5 * k * np.array(d), 1.3)
    nib.save(nib.Nifti1Image(ct, aff), out / "imaging" / "DEMO001_postimplant_CT.nii.gz")

    # PET on a coarser, shifted grid: shows that overlays are resampled through world coordinates
    paff = np.diag([4.0, 4.0, 4.0, 1.0])
    paff[:3, 3] = [-92, -110, -94]
    pshape = (46, 55, 46)
    pijk = np.indices(pshape).reshape(3, -1)
    pxyz = (paff @ np.vstack([pijk, np.ones(pijk.shape[1])]))[:3].reshape(3, *pshape)
    pr = np.sqrt((pxyz[0] / 68) ** 2 + (pxyz[1] / 85) ** 2 + ((pxyz[2] + 5) / 60) ** 2)
    hypo = np.exp(-(((pxyz - np.array([-45, -15, -20])[:, None, None, None]) ** 2).sum(0)) / (2 * 14 ** 2))
    pet = (np.where(pr < 0.95, 100.0, 0.0) + np.where((pr > 0.75) & (pr < 0.95), 40, 0)) * (1 - 0.45 * hypo)
    nib.save(nib.Nifti1Image(pet.astype(np.float32), paff), out / "imaging" / "DEMO001_FDG_PET.nii.gz")


def make_clinical(out):
    c = out / "clinical"
    c.mkdir(parents=True, exist_ok=True)
    (c / "clinical_summary.json").write_text(json.dumps({
        "synthetic": True, "patient": "DEMO001", "age": 29, "sex": "F", "handedness": "right",
        "epilepsy_onset_age": 14, "aura": "epigastric rising sensation, deja vu",
        "seizure_types": ["focal impaired awareness", "rare focal to bilateral tonic-clonic"],
        "semiology": [{"t_s": 32, "sign": "behavioural arrest"}, {"t_s": 34, "sign": "oral automatisms"},
                      {"t_s": 37, "sign": "right hand dystonic posturing"}, {"t_s": 50, "sign": "post-ictal aphasia"}],
        "medications": ["levetiracetam 1500 mg bid", "lacosamide 200 mg bid"],
        "clinical_hypothesis": "Left mesial temporal lobe epilepsy",
        # The clinicians' reading of the sEEG, typed from the epileptologist's report. The machine never sees it.
        "seizure_onset_channels": ["A1", "A2", "A3"],
        # The outside answer: what was removed and whether the patient became seizure-free.
        "surgery": {"procedure": "Left anterior temporal lobectomy", "resected_contacts": [f"{s}{k}" for s in "AB" for k in range(1, 7)],
                    "engel_class": "IA", "follow_up_months": 24},
        # Where each clinically read test points, typed from the reports.
        "clinician_readings": [
            {"test": "MRI", "points_to": "Left hippocampus", "read_by": "Radiology report"},
            {"test": "FDG-PET", "points_to": "Left temporal", "read_by": "PET report"},
            {"test": "Neuropsychology", "points_to": "Left temporal (verbal memory, naming)", "read_by": "Neuropsychologist"},
            {"test": "Semiology", "points_to": "Left temporal (right hand dystonia)", "read_by": "Epileptologist"},
        ],
    }, indent=2))
    # Contact positions and anatomical labels, as CURRY exports them after localising contacts on the CT.
    regions = {"A": "Left hippocampus", "B": "Left lateral temporal cortex", "C": "Right temporal cortex"}
    with open(out / "imaging" / "electrodes.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "x", "y", "z", "region"])
        for shaft, (x0, d) in CONTACTS.items():
            for k in range(6):
                w.writerow([f"{shaft}{k + 1}", *(np.array(x0) + 5 * k * np.array(d)).tolist(), regions[shaft]])
    (c / "MRI_report.txt").write_text(
        "SYNTHETIC REPORT. MRI 3T epilepsy protocol: subtle T1 signal change and mild volume loss of the left "
        "hippocampus, suspicious for left hippocampal sclerosis. No other lesion.\n")
    (c / "PET_report.txt").write_text(
        "SYNTHETIC REPORT. FDG-PET: left temporal hypometabolism, mesial > lateral.\n")
    rows = [("test", "domain", "score", "z"), ("RAVLT delayed recall", "verbal memory", 5, -2.1),
            ("Rey complex figure recall", "visual memory", 19, -0.3), ("Boston naming", "language", 48, -1.6),
            ("Digit span", "attention", 11, -0.2), ("Trail making B", "executive", 70, 0.1),
            ("WAIS-IV FSIQ", "general", 98, -0.1)]
    with open(c / "neuropsychology_scores.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data") / "raw" / "DEMO001"
    make_seeg_ictal(out)
    make_seeg_interictal(out)
    make_scalp_ictal(out)
    make_volumes(out)
    make_clinical(out)
    print("Synthetic patient written to", out)
