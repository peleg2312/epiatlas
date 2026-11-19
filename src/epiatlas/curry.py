"""Bridge to CURRY (Compumedics Neuroscan), the certified clinical software the team works in.

CURRY stays the clinical tool. EpiAtlas exchanges files with it in both directions:

IN   CURRY recordings (.cdt) are opened by MNE like any EDF (see ingest.py / signals.py).
     Electrode contacts localised in CURRY (from the post-implant CT) are read from a CSV/TSV
     table with columns: name, x, y, z (mm, MRI scanner space) and region (anatomical label).
     Any file whose name contains "electrode" or "contact" is read as such a table.
OUT  A copy of a recording saved as EDF+ with the machine's findings added as annotations.
     CURRY imports EDF+ like any recording, so clinicians see the findings in their own tool.
     The original recording is never changed.
"""
import csv
from pathlib import Path

import mne

from .ingest import clean_channel


def read_electrodes(path: Path) -> dict[str, dict]:
    """{'A1': {'region': 'Left hippocampus', 'xyz': [x, y, z] or None}, ...}"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
    out = {}
    for row in rows:
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        name = r.get("name") or r.get("contact") or r.get("label")
        if not name:
            continue
        try:
            xyz = [float(r["x"]), float(r["y"]), float(r["z"])]
        except (KeyError, ValueError):
            xyz = None
        out[clean_channel(name)] = {"region": r.get("region") or r.get("area") or r.get("anatomy") or "", "xyz": xyz}
    return out


def write_edf_with_findings(src: str, result: dict, out: Path) -> int:
    """Save src as EDF+ at out, keeping the clinicians' annotations and adding the machine's."""
    raw = mne.io.read_raw(src, preload=True, verbose="error")
    onsets, durations, texts = [], [], []
    for r in result.get("ictal", []):
        if r["onset_s"] is not None and r["score"] > 0:
            onsets.append(r["onset_s"]); durations.append(0.0)
            texts.append(f"EpiAtlas machine: onset {r['channel']} (score {r['score']:.2f})")
    for r in result.get("interictal", []):
        for t in r["times_s"]:
            onsets.append(t); durations.append(0.0); texts.append(f"EpiAtlas machine: spike {r['channel']}")
    ann = raw.annotations.copy()
    ann.append(onsets, durations, texts)  # same time base as the clinicians' annotations
    raw.set_annotations(ann)
    out.parent.mkdir(parents=True, exist_ok=True)
    mne.export.export_raw(out, raw, fmt="edf", overwrite=True, verbose="error")
    return len(texts)
