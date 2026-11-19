"""Step 1: find every file in a patient's folder and recognise what it is.

The raw files are never changed. We only write a list (catalog.json) saying what each file is.
The one exception is DICOM: a DICOM scan is thousands of separate slice files, so each scan
is converted once into a single NIfTI file under data/derived.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(os.environ.get("EPIATLAS_DATA", "data")).resolve()
RAW = DATA / "raw"
DERIVED = DATA / "derived"

RECORDING_TYPES = {".edf", ".bdf", ".vhdr", ".set", ".fif", ".cdt", ".con", ".sqd", ".mff", ".ds"}
FOLDER_RECORDINGS = {".ds", ".mff"}  # MEG / EGI recordings that are saved as a folder
IMAGE_TYPES = {".nii", ".nii.gz", ".mgz", ".mgh"}
VIDEO_TYPES = {".mp4", ".webm", ".mov", ".avi"}
DOCUMENT_TYPES = {".pdf", ".txt", ".md", ".csv", ".json", ".docx", ".doc", ".rtf", ".xlsx", ".png", ".jpg", ".jpeg"}

# Words in a file name that tell what kind of image or document it is.
# The first match wins. Hebrew words are included because Clalit files are often in Hebrew.
IMAGE_WORDS = [
    ("pet", r"pet|fdg|spect|פט"),
    ("ct", r"(^|[^a-z])ct([^a-z]|$)|טומוגרפיה"),
    ("fmri", r"bold|fmri|func"),
    ("dwi", r"dwi|dti"),
    ("mri", r"t1|t2|flair|mprage|mri|anat|swi|ת.?ה.?מ"),
]
DOCUMENT_WORDS = [
    ("neuropsychology", r"neuro.?psych|wais|wechsler|moca|נוירופסיכ"),
    ("imaging report", r"mri|pet|(^|[^a-z])ct([^a-z]|$)|spect|fmri|meg|radiolog|פענוח"),
    ("clinical", r"semiolog|seizure|clinical|summary|letter|discharge|סמיולוג|התקפ|סיכום|מכתב"),
]
DICOM_MODALITY = {"MR": "mri", "CT": "ct", "PT": "pet", "NM": "pet"}

# Names of scalp EEG electrodes (the 10-20 system). Old names (T3, T5) and new names (T7, P7) both occur.
SCALP_10_20 = {"FP1", "FP2", "FPZ", "F7", "F3", "FZ", "F4", "F8", "T7", "T3", "C3", "CZ", "C4", "T8", "T4",
               "P7", "T5", "P3", "PZ", "P4", "P8", "T6", "O1", "OZ", "O2", "A1", "A2", "M1", "M2",
               "F9", "F10", "T9", "T10", "P9", "P10", "FT9", "FT10", "AF3", "AF4", "FC1", "FC2", "CP1", "CP2"}


def file_type(path):
    """'.edf', '.csv' ... and '.nii.gz' as one type."""
    if path.name.lower().endswith(".nii.gz"):
        return ".nii.gz"
    return path.suffix.lower()


def clean_channel(name):
    """Hospital exports decorate channel names: 'EEG Fp1-Ref' -> 'FP1'."""
    name = re.sub(r"^(EEG|SEEG|POL)\s+", "", name.strip(), flags=re.I)
    name = re.sub(r"[-_ ]?(REF|AV|AVG|LE)$", "", name, flags=re.I)
    return name.upper()


def find_word(text, word_table, default):
    text = text.lower()
    for label, pattern in word_table:
        if re.search(pattern, text):
            return label
    return default


def seizure_or_not(text):
    """'ictal' (a seizure), 'interictal' (between seizures) or 'unknown', from a name or annotation."""
    text = text.lower()
    if re.search(r"inter.?ictal|baseline|sleep|בין.?התקפ", text):
        return "interictal"
    if re.search(r"seiz|(^|[^a-z])sz|ictal|התקף", text):
        return "ictal"
    return "unknown"


def is_dicom(path):
    """DICOM files often have no extension. They contain the letters 'DICM' at byte 128."""
    if path.suffix.lower() == ".dcm":
        return True
    if path.suffix:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def make_id(text):
    """A short, stable id for a file, so the screen can ask for it without sending a file path."""
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def describe_recording(path, relative_path):
    import mne  # slow to import, so only when there is a recording
    raw = mne.io.read_raw(path, preload=False, verbose="error")
    names = raw.ch_names

    if {"mag", "grad"} & set(raw.get_channel_types()):
        modality = "meg"
    else:
        # Scalp EEG if more than half of the channels have scalp electrode names, otherwise sEEG
        n_scalp = sum(1 for n in names if clean_channel(n) in SCALP_10_20)
        modality = "eeg" if n_scalp > len(names) / 2 else "seeg"

    notes = [a["description"] for a in raw.annotations]
    state = seizure_or_not(relative_path)  # folder names count too, e.g. "seizures/3.edf"
    if state == "unknown":
        state = seizure_or_not(" ".join(notes))

    return {"modality": modality, "state": state, "sfreq": raw.info["sfreq"], "n_channels": len(names),
            "duration_s": round(raw.times[-1], 2), "n_annotations": len(notes)}


def find_dcm2niix():
    """The DICOM converter, installed with the Python packages (requirements.txt) or on the system."""
    next_to_python = Path(sys.executable).parent / "dcm2niix"
    if next_to_python.exists():
        return str(next_to_python)
    return shutil.which("dcm2niix")


def convert_dicom_series(patient_id, files):
    import pydicom
    header = pydicom.dcmread(files[0], stop_before_pixels=True)
    description = f"{getattr(header, 'SeriesDescription', '')} {getattr(header, 'ProtocolName', '')}"
    scanner_type = DICOM_MODALITY.get(getattr(header, "Modality", ""), "mri")
    series_uid = str(getattr(header, "SeriesInstanceUID", files[0].parent))

    info = {"modality": find_word(description, IMAGE_WORDS, scanner_type),
            "series": description.strip() or series_uid, "n_slices": len(files),
            "date": str(getattr(header, "StudyDate", ""))}

    out_folder = DERIVED / patient_id / "nifti"
    out_name = make_id(series_uid)
    out_file = out_folder / f"{out_name}.nii.gz"
    if not out_file.exists():
        converter = find_dcm2niix()
        if not converter:
            info["note"] = "Install dcm2niix to view this DICOM series."
            return info
        out_folder.mkdir(parents=True, exist_ok=True)
        subprocess.run([converter, "-z", "y", "-f", out_name, "-o", str(out_folder), str(files[0].parent)],
                       capture_output=True)
    if out_file.exists():
        info["derived"] = str(out_file.relative_to(DATA))
    else:
        info["note"] = "dcm2niix could not convert this series"
    return info


def recognise(path, relative_path):
    """One catalog entry for a file, or None if it is not one we show."""
    kind_of_file = file_type(path)
    item = {"id": make_id(relative_path), "name": path.name, "path": relative_path}

    if kind_of_file in RECORDING_TYPES:
        return {**item, "kind": "ephys", **describe_recording(path, relative_path)}
    if kind_of_file in IMAGE_TYPES:
        return {**item, "kind": "image", "modality": find_word(relative_path, IMAGE_WORDS, "mri")}
    if kind_of_file in VIDEO_TYPES:
        return {**item, "kind": "video", "modality": "semiology video"}
    if kind_of_file in {".csv", ".tsv"} and re.search(r"electrode|contact", path.name, re.I):
        return {**item, "kind": "electrodes", "modality": "electrode contacts", "format": kind_of_file[1:]}
    if kind_of_file in DOCUMENT_TYPES:
        return {**item, "kind": "document", "modality": find_word(path.name, DOCUMENT_WORDS, "other"),
                "format": kind_of_file[1:]}
    return None


def ingest(patient_id):
    folder = RAW / patient_id
    items, dicom_folders, skipped = [], {}, []

    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts):
            continue  # hidden files
        if any(parent.suffix.lower() in FOLDER_RECORDINGS for parent in relative.parents):
            continue  # a file inside a folder-recording; the folder itself is the recording
        if path.is_dir() and file_type(path) not in FOLDER_RECORDINGS:
            continue

        try:
            item = recognise(path, str(relative))
        except Exception as error:  # one broken file must not hide the rest of the patient
            item = {"id": make_id(str(relative)), "name": path.name, "path": str(relative),
                    "kind": "unreadable", "modality": "?", "error": str(error)[:200]}

        if item:
            items.append(item)
        elif path.is_file() and is_dicom(path):
            dicom_folders.setdefault(path.parent, []).append(path)
        elif path.is_file():
            skipped.append(str(relative))

    for dicom_folder, files in dicom_folders.items():
        relative = str(dicom_folder.relative_to(folder))
        items.append({"id": make_id(relative), "name": relative + "/", "path": relative, "kind": "image",
                      "format": "dicom", **convert_dicom_series(patient_id, files)})

    catalog = {"patient": patient_id, "items": items, "skipped": skipped}
    out = DERIVED / patient_id / "catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    return catalog


def load_catalog(patient_id):
    saved = DERIVED / patient_id / "catalog.json"
    if saved.exists():
        return json.loads(saved.read_text())
    return ingest(patient_id)


def resolve(patient_id, item):
    """Full path of a catalog item. Built only from the catalog, never from what the browser sends."""
    if "derived" in item:
        return DATA / item["derived"]
    return RAW / patient_id / item["path"]
