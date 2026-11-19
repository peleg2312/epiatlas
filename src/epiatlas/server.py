"""Step 7: a small web server that connects the screen (static/) to the steps above.
Run: uvicorn epiatlas.server:app (from src/). It only listens on this computer (127.0.0.1)."""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import analysis, bias, curry, imaging, signals, summary
from .ingest import DATA, DERIVED, RAW, ingest, load_catalog, resolve

app = FastAPI(title="EpiAtlas")


@app.middleware("http")
async def always_fresh(request, call_next):
    """Tell the browser to check for a newer version of every file, so updates show after a normal reload."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- Safety checks: the browser can only reach files listed in a patient's catalog ----------
def patient(patient_id):
    # Letters, digits, '_', '-', '.', but not starting with '.', so '..' can't leave the data folder
    if not re.fullmatch(r"\w[\w.-]*", patient_id) or not (RAW / patient_id).is_dir():
        raise HTTPException(404, f"No patient folder data/raw/{patient_id}")
    return patient_id


def item(patient_id, item_id, kind=None):
    found = None
    for entry in load_catalog(patient(patient_id))["items"]:
        if entry["id"] == item_id:
            found = entry
    if not found or (kind and found["kind"] != kind):
        raise HTTPException(404, "Item not in this patient's catalog")
    path = resolve(patient_id, found)
    if not path.resolve().is_relative_to(DATA) or not path.exists():
        raise HTTPException(404, found.get("note", "File missing"))
    return found, str(path)


# ---------- Patients and files ----------
@app.get("/api/patients")
def patients():
    out = []
    if RAW.exists():
        for folder in sorted(RAW.iterdir()):
            if folder.is_dir() and not folder.name.startswith("."):
                items = load_catalog(folder.name)["items"]
                out.append({"id": folder.name, "modalities": sorted({i["modality"] for i in items}),
                            "n_items": len(items)})
    return out


@app.get("/api/patients/{patient_id}")
def catalog(patient_id: str):
    return load_catalog(patient(patient_id))


@app.post("/api/patients/{patient_id}/ingest")
def rescan(patient_id: str):
    return ingest(patient(patient_id))


@app.get("/api/patients/{patient_id}/file/{item_id}")
def raw_file(patient_id: str, item_id: str):
    found, path = item(patient_id, item_id)
    if found["kind"] not in {"document", "video"}:
        raise HTTPException(400, "Only documents and videos are served as files")
    return FileResponse(path, filename=found["name"], content_disposition_type="inline")


# ---------- Recordings ----------
@app.get("/api/patients/{patient_id}/ephys/{item_id}/info")
def ephys_info(patient_id: str, item_id: str):
    return signals.info(item(patient_id, item_id, "ephys")[1])


@app.get("/api/patients/{patient_id}/ephys/{item_id}/window")
def ephys_window(patient_id: str, item_id: str, start: float = Query(0, ge=0), dur: float = Query(10, gt=0, le=600),
                 montage: str = Query("raw", pattern="^(raw|average|bipolar)$"),
                 lo: float | None = Query(None, gt=0), hi: float | None = Query(None, gt=0),
                 notch: float | None = Query(None, gt=0)):
    path = item(patient_id, item_id, "ephys")[1]
    return signals.read_window(path, start, dur, montage, low=lo, high=hi, notch=notch)


# ---------- Images ----------
@app.get("/api/patients/{patient_id}/image/{item_id}/info")
def image_info(patient_id: str, item_id: str):
    return imaging.volume_info(item(patient_id, item_id, "image")[1])


@app.get("/api/patients/{patient_id}/image/{item_id}/slice")
def image_slice(patient_id: str, item_id: str, axis: str = Query(pattern="^(axial|coronal|sagittal)$"),
                index: int = 0, overlay: str | None = None):
    base = item(patient_id, item_id, "image")[1]
    over = item(patient_id, overlay, "image")[1] if overlay else None
    pixels, index = imaging.slice_bytes(base, axis, index, over)
    return Response(pixels.tobytes(), media_type="application/octet-stream",
                    headers={"X-Rows": str(pixels.shape[0]), "X-Cols": str(pixels.shape[1]), "X-Index": str(index)})


@app.get("/api/patients/{patient_id}/image/{item_id}/contacts")
def image_contacts(patient_id: str, item_id: str):
    """Electrode contacts (positions from CURRY) as voxels of this image, to draw them as dots."""
    data, affine, _, _ = imaging.load(item(patient_id, item_id, "image")[1])
    millimetres_to_voxel = np.linalg.inv(affine)
    out = []
    for name, contact in summary.electrodes(load_catalog(patient_id), patient_id).items():
        if not contact["xyz"]:
            continue
        voxel = np.rint(millimetres_to_voxel @ [*contact["xyz"], 1])[:3].astype(int)
        if all(0 <= v < size for v, size in zip(voxel, data.shape)):
            out.append({"name": name, "region": contact["region"], "ijk": voxel.tolist()})
    return out


# ---------- The machine ----------
def saved_result_name(item_id, path, start):
    """The machine's result is saved, so pages open fast. The file name includes the settings and the
    recording's last-change time, so changing either one makes the machine read the file again."""
    version = json.dumps(analysis.SETTINGS) + str(Path(path).stat().st_mtime)
    return f"{item_id}_{start:g}_{hashlib.sha1(version.encode()).hexdigest()[:8]}.json"


@app.get("/api/patients/{patient_id}/analysis/{item_id}")
def run_analysis(patient_id: str, item_id: str, start: float = Query(0, ge=0)):
    found, path = item(patient_id, item_id, "ephys")
    saved = DERIVED / patient_id / "analysis" / saved_result_name(item_id, path, start)
    if saved.exists():
        result = json.loads(saved.read_text())
    else:
        result = analysis.analyse(path, found["modality"], found["state"], start)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps(result))
    contacts = summary.electrodes(load_catalog(patient_id), patient_id)
    return summary.label_rows(result, found["modality"], contacts)


@app.get("/api/patients/{patient_id}/analysis/{item_id}/compare")
def compare(patient_id: str, item_id: str):
    """Called only when the user presses "Show doctors' reading", after the blind result is on screen."""
    result = run_analysis(patient_id, item_id, 0)
    info = summary.clinical_info(load_catalog(patient_id), patient_id)
    doctors = info.get("seizure_onset_channels") or []
    if not doctors:
        return {"clinical": [], "note": "No doctors' reading for this patient yet. "
                                        "Add \"seizure_onset_channels\" to a JSON file in the patient folder."}
    return analysis.concordance(result.get("ictal") or result.get("interictal") or [], doctors)


@app.post("/api/patients/{patient_id}/analysis/{item_id}/export")
def export_for_curry(patient_id: str, item_id: str):
    """Save a copy of the recording with the machine's findings as EDF+ markers, to open in CURRY."""
    found, path = item(patient_id, item_id, "ephys")
    out = DERIVED / patient_id / "for_curry" / f"{Path(found['name']).stem}_with_EpiAtlas_findings.edf"
    n_markers = curry.write_edf_with_findings(path, run_analysis(patient_id, item_id, 0), out)
    return {"path": str(out.relative_to(DATA.parent)), "annotations": n_markers}


@app.get("/api/patients/{patient_id}/summary")
def patient_summary(patient_id: str):
    return summary.build(patient(patient_id), load_catalog(patient_id),
                         lambda item_id: run_analysis(patient_id, item_id, 0))


@app.get("/api/study")
def study():
    """Across all patients with a known surgery outcome: was each method's start area removed?"""
    rows = []
    for patient_info in patients():
        patient_id = patient_info["id"]
        catalog = load_catalog(patient_id)
        result = None
        for entry in catalog["items"]:
            if entry["kind"] == "ephys" and entry["modality"] == "seeg" and entry["state"] == "ictal":
                result = run_analysis(patient_id, entry["id"], 0)
                break
        row = bias.study_row(patient_id, summary.clinical_info(catalog, patient_id), result)
        if row:
            rows.append(row)
    return bias.study_summary(rows)


# The screen itself (must come last, so it doesn't hide the /api addresses)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
