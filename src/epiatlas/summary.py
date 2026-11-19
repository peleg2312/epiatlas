"""Step 5: the patient page. Where each test points in the brain, and whether the tests agree.

Two groups are kept apart on purpose (see bias.py):
  blind  - the machine read the raw data, without seeing any report
  report - the doctors' readings, typed from their reports
"""
import json
import re
from collections import Counter

from . import bias, curry
from .ingest import clean_channel, resolve

# Scalp electrode -> brain area. Old (T3, T5) and new (T7, P7) names both appear in hospital files.
SCALP_REGION = {
    "FP1": "Left frontal", "F3": "Left frontal", "C3": "Left central", "P3": "Left parietal", "O1": "Left occipital",
    "FP2": "Right frontal", "F4": "Right frontal", "C4": "Right central", "P4": "Right parietal", "O2": "Right occipital",
}
for name in ["F7", "T7", "T3", "P7", "T5", "FT9", "T9", "P9", "A1", "M1"]:
    SCALP_REGION[name] = "Left temporal"
for name in ["F8", "T8", "T4", "P8", "T6", "FT10", "T10", "P10", "A2", "M2"]:
    SCALP_REGION[name] = "Right temporal"

# Words in an area name -> lobe. 'Left hippocampus' -> temporal lobe.
LOBE_WORDS = [("hippocamp", "temporal"), ("amygdal", "temporal"), ("temporal", "temporal"),
              ("frontal", "frontal"), ("parietal", "parietal"), ("occipital", "occipital"),
              ("insul", "insular"), ("central", "central")]


def clinical_info(catalog, patient_id):
    """All the patient's JSON documents merged into one dictionary."""
    info = {}
    for item in catalog["items"]:
        if item["kind"] == "document" and item.get("format") == "json":
            try:
                data = json.loads(resolve(patient_id, item).read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(data, dict):
                info.update(data)
    return info


def electrodes(catalog, patient_id):
    """Electrode contacts (position and brain area), as exported from CURRY."""
    contacts = {}
    for item in catalog["items"]:
        if item["kind"] == "electrodes":
            contacts.update(curry.read_electrodes(resolve(patient_id, item)))
    return contacts


def contacts_of(channel):
    """'A1-A2' -> ['A1', 'A2'];  'EEG T7-Ref' -> ['T7'] ('-Ref' is a reference, not a second contact)."""
    return [c for c in clean_channel(channel).split("-") if c]


def compact(contacts):
    """['A1', 'A2', 'A3', 'C4'] -> 'A1–A3, C4'"""
    numbers_by_electrode = {}
    for contact in contacts:
        match = re.match(r"^(.*?)(\d+)$", contact)
        if match:
            numbers_by_electrode.setdefault(match.group(1), []).append(int(match.group(2)))
        else:
            numbers_by_electrode.setdefault(contact, [])

    parts = []
    for electrode, numbers in numbers_by_electrode.items():
        if not numbers:
            parts.append(electrode)
            continue
        numbers = sorted(set(numbers))
        run_start = previous = numbers[0]
        for n in numbers[1:] + [None]:  # None closes the last run
            if n is not None and n == previous + 1:
                previous = n
                continue
            if run_start == previous:
                parts.append(f"{electrode}{run_start}")
            else:
                parts.append(f"{electrode}{run_start}–{electrode}{previous}")
            run_start = previous = n
    return ", ".join(parts)


def region(contact, modality, contacts):
    if modality == "seeg":
        return contacts.get(contact, {}).get("region", "")
    return SCALP_REGION.get(contact, "")


def label_rows(result, modality, contacts):
    """Add the brain area to every channel the machine ranked."""
    for key in ["ictal", "interictal"]:
        for row in result.get(key, []):
            row["region"] = ""
            for contact in contacts_of(row["channel"]):
                area = region(contact, modality, contacts)
                if area:
                    row["region"] = area
                    break
    return result


def machine_row(item, result, contacts):
    """One table row for a recording: where its top 3 channels are."""
    if "ictal" in result:
        rows, score_name, test = result["ictal"], "score", "seizure"
    else:
        rows, score_name, test = result.get("interictal", []), "per_min", "interictal spikes"
    top = [row for row in rows[:3] if row[score_name] > 0]
    if not top:
        return None

    flagged = sorted({c for row in top for c in contacts_of(row["channel"])})
    areas = Counter()
    for contact in flagged:
        area = region(contact, item["modality"], contacts)
        if area:
            areas[area] += 1
    onsets = [row["onset_s"] for row in top if row.get("onset_s") is not None]
    edge = []
    if item["modality"] == "seeg" and test == "seizure":
        edge = bias.at_edge(contacts_of(top[0]["channel"]), contacts)

    kind = {"seeg": "sEEG", "eeg": "Scalp EEG", "meg": "MEG"}.get(item["modality"], item["modality"])
    return {"test": f"{kind} {test}",
            "points_to": areas.most_common(1)[0][0] if areas else "Area unknown (no contact locations)",
            "detail": compact(flagged), "read_by": "Machine", "source": "machine", "item": item["id"],
            "onset_s": min(onsets) if onsets else None, "edge": edge}


def side_and_lobe(text):
    """'Left hippocampus' -> ('left', 'temporal')"""
    text = text.lower()
    side = "left" if "left" in text else "right" if "right" in text else None
    lobe = None
    for word, lobe_name in LOBE_WORDS:
        if word in text:
            lobe = lobe_name
            break
    return side, lobe


def agreement(rows):
    """'All 5 point to the left side, 4 of them to the temporal lobe.'
    Side first, then lobe, because some blind readings (PET) name a side but not a lobe."""
    if not rows:
        return ""
    places = [side_and_lobe(row["points_to"]) for row in rows]
    sides = Counter(side for side, _ in places if side)
    if not sides:
        return "No clear side."
    side, n_side = sides.most_common(1)[0]
    text = f"All {n_side}" if n_side == len(rows) else f"{n_side} of {len(rows)}"
    text += f" point to the {side} side"

    lobes = Counter(lobe for s, lobe in places if s == side and lobe)
    if lobes:
        lobe, n_lobe = lobes.most_common(1)[0]
        text += f", {'all' if n_lobe == n_side else n_lobe} of them to the {lobe} lobe"
    return text + "."


def build(patient_id, catalog, analyse):
    """analyse(item_id) gives the machine's (cached) reading of one recording."""
    info = clinical_info(catalog, patient_id)
    contacts = electrodes(catalog, patient_id)

    blind = []
    for item in catalog["items"]:
        if item["kind"] == "ephys":
            row = machine_row(item, analyse(item["id"]), contacts)
            if row:
                blind.append(row)
    # seizures first, sEEG before scalp
    blind.sort(key=lambda row: ("seizure" not in row["test"], not row["test"].startswith("sEEG")))
    blind += bias.blind_rows(catalog, patient_id, info)

    report = [{**row, "source": "clinician"} for row in info.get("clinician_readings", [])]
    return {"clinical": info, "rows": blind + report,
            "agreement": {"blind": agreement(blind), "report": agreement(report)}}
