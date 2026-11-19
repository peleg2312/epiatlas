"""Step 6: reducing bias, and measuring it.

1. Blind readings of more tests (PET, neuropsychology): the machine reads the numbers in the
   file, never the report, so its answer cannot be swayed by the other tests.
2. Edge warning: sEEG only records where electrodes were placed. If the strongest channel is at
   the outer edge of the implanted area, the real start may be outside it.
3. Outcome check across patients: was each method's start area removed in the patients who
   became seizure-free (Engel I)? This is the only way to measure bias, and it needs many patients.
"""
import csv
import re

import numpy as np
from scipy.ndimage import gaussian_filter

from . import imaging
from .ingest import clean_channel, resolve

# Settings, fixed in advance and the same for every patient
PET_MIN_DIFFERENCE = 0.10   # one side must be at least 10% lower than its mirror point
PET_SMOOTH_MM = 8.0         # average over ~8 mm so single noisy voxels don't decide
NEURO_LOW_Z = -1.5          # z <= -1.5 counts as an impaired score
EDGE_TOLERANCE_MM = 3.0

# Published rule for temporal lobe epilepsy. 'dominant' = the language side, usually the left.
# If fMRI / Wada showed right-sided language, put "language_dominance": "right" in the clinical JSON.
NEURO_RULE = {"verbal memory": "dominant", "language": "dominant", "naming": "dominant",
              "visual memory": "other", "visuospatial memory": "other"}


# ---------- 1. Blind readings ----------
def pet_reading(path):
    """Compare every point of the brain with its mirror point on the other side.
    In FDG-PET, the area where seizures start usually takes up less sugar (it is darker)."""
    data, _, _, voxel_size = imaging.load(path)
    brain = data > 0.3 * np.percentile(data[data > 0], 99)

    # The midline: the centre of the brain from left to right.
    # Limitation: assumes the head lies straight in the scanner.
    midline = int(round(np.nonzero(brain)[0].mean()))
    half = min(midline, data.shape[0] - midline - 1)

    # Smooth inside the brain only (dividing by the smoothed mask stops the edges from darkening)
    sigma = [PET_SMOOTH_MM / size for size in voxel_size]
    smooth = gaussian_filter(data * brain, sigma) / (gaussian_filter(brain.astype(float), sigma) + 1e-6)

    # In RAS orientation x grows to the right, so lower x = the left side.
    # Flip the left half so that each left point lines up with its mirror point on the right.
    left = smooth[midline - half:midline][::-1]
    right = smooth[midline + 1:midline + 1 + half]
    inside_both = brain[midline - half:midline][::-1] & brain[midline + 1:midline + 1 + half]
    difference = ((left - right) / ((left + right) / 2 + 1e-6))[inside_both]  # negative = left is lower

    left_lower = -np.percentile(difference, 2)   # the most negative values (ignoring the extreme 2%)
    right_lower = np.percentile(difference, 98)  # the most positive values
    if max(left_lower, right_lower) < PET_MIN_DIFFERENCE:
        return {"points_to": "No clear left-right difference",
                "detail": f"largest difference {max(left_lower, right_lower):.0%}"}
    if left_lower > right_lower:
        side, size = "Left", left_lower
    else:
        side, size = "Right", right_lower
    return {"points_to": f"{side} side",
            "detail": f"{size:.0%} lower than the mirror side. Lobe not named: needs a brain atlas"}


def neuropsych_reading(path, language_side="left"):
    """Use the raw test scores (not the neuropsychologist's conclusion) with the fixed rule above."""
    other_side = "right" if language_side == "left" else "left"
    votes = {"left": 0, "right": 0}
    low_scores = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {key.strip().lower(): (value or "").strip() for key, value in row.items() if key}
            try:
                z = float(row.get("z", ""))
            except ValueError:
                continue
            rule = NEURO_RULE.get(row.get("domain", "").lower())
            if z <= NEURO_LOW_Z and rule:
                side = language_side if rule == "dominant" else other_side
                votes[side] += 1
                low_scores.append(f"{row['domain']} {z:+.1f}")

    if votes["left"] == votes["right"] == 0:
        return {"points_to": "No lateralising deficit", "detail": ""}
    if votes["left"] == votes["right"]:
        return {"points_to": "Both sides (unclear)", "detail": ", ".join(low_scores)}
    side = "left" if votes["left"] > votes["right"] else "right"
    return {"points_to": f"{side.capitalize()} temporal", "detail": ", ".join(low_scores)}


def blind_rows(catalog, patient_id, info):
    """Machine readings of the tests that are not EEG, as rows for the patient page."""
    rows = []
    for item in catalog["items"]:
        if item["kind"] == "image" and item["modality"] == "pet" and not item.get("note"):
            rows.append({"test": "FDG-PET", **pet_reading(str(resolve(patient_id, item)))})
            break
    language_side = "right" if str(info.get("language_dominance", "")).lower().startswith("r") else "left"
    for item in catalog["items"]:
        if item["kind"] == "document" and item["modality"] == "neuropsychology" and item.get("format") == "csv":
            rows.append({"test": "Neuropsychology", **neuropsych_reading(resolve(patient_id, item), language_side)})
            break
    for row in rows:
        row.update({"read_by": "Machine", "source": "machine", "onset_s": None})
    return rows


# ---------- 2. Edge of the implanted area ----------
def at_edge(names, contacts):
    """Contacts that are the outermost of all implanted contacts, in their own direction from the centre."""
    positions = {name: np.array(c["xyz"]) for name, c in contacts.items() if c.get("xyz")}
    if len(positions) < 4:
        return []
    centre = np.mean(list(positions.values()), axis=0)
    all_from_centre = np.array(list(positions.values())) - centre

    edge = []
    for name in names:
        if name not in positions:
            continue
        direction = positions[name] - centre
        distance = np.linalg.norm(direction)
        if distance == 0:
            continue
        # How far out does any contact reach in this direction? If no contact reaches
        # clearly further than this one, this contact is on the edge.
        furthest = (all_from_centre @ direction).max() / distance
        if furthest <= distance + EDGE_TOLERANCE_MM:
            edge.append(name)
    return edge


# ---------- 3. Outcome check across patients ----------
def seizure_free(engel_class):
    """Engel class I (IA-ID) means seizure-free. Accepts 'IA', 'Engel IA', '1a' ..."""
    text = str(engel_class).upper().replace("ENGEL", "").strip()
    return bool(re.fullmatch(r"(I|1)[A-D]?", text))


def share_removed(zone, removed):
    """Share of a start area (set of contacts) that the surgeon removed."""
    if not zone:
        return None
    return round(len(zone & removed) / len(zone), 2)


def study_row(patient_id, info, result):
    """One patient: what share of the machine's and of the doctors' start area was removed."""
    surgery = info.get("surgery") or {}
    removed = {clean_channel(c) for c in surgery.get("resected_contacts", [])}
    if not removed or "engel_class" not in surgery or not result or not result.get("ictal"):
        return None

    machine_zone = set()  # fixed rule: the contacts of the machine's top 3 channels
    for row in result["ictal"][:3]:
        if row["score"] > 0:
            machine_zone.update(c for c in clean_channel(row["channel"]).split("-") if c)
    doctors_zone = {clean_channel(c) for c in info.get("seizure_onset_channels", [])}

    return {"patient": patient_id, "engel": str(surgery["engel_class"]),
            "seizure_free": seizure_free(surgery["engel_class"]),
            "machine_removed": share_removed(machine_zone, removed),
            "doctors_removed": share_removed(doctors_zone, removed)}


def study_summary(rows):
    free = [row for row in rows if row["seizure_free"]]
    return {"patients": rows, "n": len(rows), "n_seizure_free": len(free),
            "machine_removed_in_seizure_free": sum(1 for row in free if row["machine_removed"] == 1.0),
            "doctors_removed_in_seizure_free": sum(1 for row in free if row["doctors_removed"] == 1.0)}
