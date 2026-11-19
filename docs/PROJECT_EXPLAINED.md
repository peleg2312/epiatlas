# EpiAtlas: what was built, how it works, and why

## 1. The project in plain words

Some epilepsy patients don't respond to medication. For them, surgery can
remove the small area where seizures start. To find it, the hospital runs many
tests (EEG, sEEG, MRI, PET, neuropsychology), each in a different system and
read by a different doctor.

The professor's idea has two parts, and the program has one page for each:

1. **Patient page:** all of one patient's tests in one place.
2. **Machine page:** a computer reads the raw data **blind**, without knowing
   what the doctors concluded. Then it compares its answer with theirs.
   Disagreement may mean the doctors' reading was biased, or the machine is
   wrong. Either way it's worth studying.

## 2. Why it was built this way

| | CURRY | Python (chosen) |
|---|---|---|
| Clinically certified | Yes | No |
| EEG + MRI + CT + PET together | Yes | Yes |
| Clinical picture, semiology, neuropsychology | No | Yes |
| Transparent, editable analysis | No (closed program) | Yes |
| Many patients, machine learning | No | Yes |
| Cost | Licence | Free |

**CURRY stays the clinical tool**, as the professor prefers. A blind machine
can't run inside a closed program, so the research engine is in Python. The
two exchange files:

- **In:** CURRY recordings (.cdt), and CURRY's electrode positions (a table of
  name, x, y, z, region).
- **Out:** "Send to CURRY" saves a copy of a recording with the machine's
  findings as markers. Doctors open it in CURRY. The original is never changed.

CURRY still does things EpiAtlas doesn't:
- aligning images from different days;
- finding the electrodes on the CT;
- EEG/MEG source localisation;
- clinical certification.

Other decisions:
- **Raw data stays raw.** Files are read in place and never changed.
- **Runs only on this computer.** No internet, because this is patient data.
- **Colours:** amber = doctors, teal = machine.
- **No de-identification yet.** This was postponed on purpose.

## 3. How to run it

Open Terminal in the project folder and run `./run.sh`, then open
**http://127.0.0.1:8000**. The first run installs everything and creates a
synthetic demo patient. Stop it with `Ctrl+C`. On a lab computer,
`docker compose up --build` does the same.

**Adding a real patient:** make a folder `data/raw/<patient>/` and drop the
files in, in any layout: EDF, DICOM folders, NIfTI, PDF, CSV. Then reload the
page. Put "seizure" or "interictal" in recording names (Hebrew works too).

Type into a JSON file in the folder (the machine never reads these; they are
only used for comparison):

| Key | Example | Used for |
|---|---|---|
| `seizure_onset_channels` | `["A1", "A2", "A3"]` | The doctors' sEEG reading |
| `clinician_readings` | `[{"test": "MRI", "points_to": "Left hippocampus"}]` | The report rows on page 1 |
| `surgery` | `{"resected_contacts": ["A1", …], "engel_class": "IA"}` | The outcome check |
| `language_dominance` | `"right"` (only if fMRI/Wada showed it) | The neuropsychology rule |

Electrode positions from CURRY go in a CSV with "electrodes" in its name and
the columns `name, x, y, z, region`.

## 4. The two pages

**Page 1: Patient.** A table in two groups:
- **Blind:** what the machine read from the raw data (EEG, sEEG, PET image,
  neuropsychology scores).
- **From reports:** the doctors' readings.

Each group has its own agreement line, for example "All 5 point to the left
side, 4 of them to the temporal lobe". Click a row to see the test itself:
- EEG traces, opening at the seizure;
- the MRI with PET on top and the electrodes as dots;
- the neuropsychology scores;
- the seizure timeline.

**Page 2: Machine.** The details of one recording:
- the machine's answer ("Seizure starts in A1–A3 at 0:30") and its top
  channels;
- a warning if the start is at the edge of the electrodes;
- **Show doctors' reading**, which reveals the comparison and the
  across-patients outcome table;
- **Send to CURRY**.

## 5. How it works inside

| File | Step |
|---|---|
| `ingest.py` | 1. Finds every file and recognises it (recording, image, document, video), in English and Hebrew. Converts DICOM to NIfTI. |
| `signals.py` | 2. Reads recordings, leaves out non-brain channels (ECG, EOG…), applies montage and filters. |
| `analysis.py` | 3. The machine (section 6). |
| `imaging.py` | 4. Cuts image slices and lays PET/CT over the MRI using each file's scanner coordinates. |
| `summary.py` | 5. Builds the page 1 table and agreement lines. |
| `bias.py` | 6. Blind PET and neuropsychology readings, edge warning, outcome check. |
| `server.py` | 7. Small local web server connecting the screen to the steps. |
| `curry.py` | Reads CURRY's electrode table, writes the CURRY export. |
| `static/` | The screen (HTML/JavaScript). |

Main libraries: MNE-Python (EEG/sEEG/MEG), nibabel and pydicom (images),
SciPy (signal processing), FastAPI (web server).

## 6. The machine

It receives only the raw signal. Before analysis it removes 50 Hz mains noise
and slow drift, and uses a bipolar montage for sEEG (an average reference for
scalp EEG).

- **sEEG seizure: Epileptogenicity Index** (Bartolomei, *Brain* 2008). The
  onset zone starts with fast activity (beta/gamma), and starts first.
  1. For each channel, the ratio of fast to slow energy is computed every
     0.25 s.
  2. A change detector (Page–Hinkley) finds when that ratio rises for good.
  3. EI = how strong the fast activity is ÷ how late the channel started.
  4. Channels reached late, or only by slow rhythms, score low, because that
     is spread, not origin.
- **Scalp EEG / MEG seizure: line length** (Esteller 2001). This measures how
  much the signal moves each second. On the scalp, fast activity is mostly
  muscle, so EI doesn't fit there.
- **Between seizures: spikes per minute.** Sharp peaks more than 6 robust
  standard deviations above the channel's normal level.
- **Comparison (only after the reveal):** how many of the doctors' contacts
  the machine also flagged, and which extra ones it found.

All thresholds are in `SETTINGS` in `analysis.py`. Tune them once on real
recordings, then **freeze** them. Changing them while looking at results
brings the bias back.

## 7. Bias

**Bias** is an error that always pushes the answer the same way. Unlike random
error, it doesn't cancel out over many patients.

| Bias | What happens | What the program does |
|---|---|---|
| **Anchoring** | Knowing the MRI result shapes how the EEG is read. | **Blind readings** from raw files only. The doctors' reading stays hidden until you ask. |
| **Fake agreement** | Readings that influenced each other look like independent agreement. | Blind and report readings are **counted separately**. |
| **Bent rules** | Thresholds adjusted until the result "looks right". | **Fixed published methods**, the same for every patient. |
| **Electrode placement** | sEEG only sees where electrodes were put. | Can't be fixed, only **flagged**: the edge warning. |
| **Circular answer** | The removed area was chosen with the same tests. | An **outside answer**: was the patient seizure-free (Engel I)? |

The blind readings outside EEG:
- **PET:** each side of the brain is compared with its mirror. If one side is
  at least 10% lower, the reading names that side. It names the side only;
  naming the lobe needs a brain atlas.
- **Neuropsychology:** starts from raw z-scores and uses a published rule.
  Low verbal memory or naming points to the language side (usually left).
  Low visual memory points to the other side.

**Outcome check (page 2):** for each operated patient, it shows what share of
the machine's area and of the doctors' area was removed. In seizure-free
patients, the method whose area was removed more often was probably right
more often. **This needs tens of patients to mean anything.**

What the program can't remove:
- where electrodes were implanted;
- which patients got surgery at all;
- bias already in the raw data.

MRI and semiology still have only the doctors' readings.

## 8. Demo patient and what was checked

**DEMO001 is entirely synthetic:** a left mesial temporal lobe epilepsy. It
has sEEG electrodes A (left hippocampus), B (left lateral temporal) and C
(right temporal), with the seizure starting at 30 s in A1–A3. It also has a
left temporal scalp seizure, spikes, a simple phantom MRI/CT/PET, reports,
neuropsychology scores, and a made-up seizure-free surgery.

An automated check rebuilds the demo patient and confirms:
- every file is recognised;
- EI ranks A1–A3 first (onset 29.75 s vs. the true 30 s);
- line length finds F7/T7/P7;
- PET and neuropsychology point left, and a symmetric brain gives no side;
- the export to CURRY keeps the doctors' marks;
- files outside a patient's folder can't be reached;
- a DICOM scan converts correctly.

**These results show the software works as designed. They are not evidence
that the method works on real patients.** That is the research itself.

## 9. Limitations and next steps

**Limitations:**
- Research only, not a medical device.
- Untested on real Clalit data.
- Images from different days are not aligned yet.
- Only the first 10 minutes of a recording are analysed.
- No de-identification yet.

**Next steps:**
1. **Pilot with 2–3 real anonymised patients.**
2. **Test the CURRY round trip with the team.**
3. **Align images:** use the aligned images CURRY exports.
4. **De-identification,** under the ethics (Helsinki) approval.
5. **Many past patients with surgical outcome.** Only then consider machine
   learning.

## 10. Talking points

- **Problem:** each test is read separately, and each reading already
  contains interpretation.
- **Built:** one local viewer for all of a patient's data, plus a blind
  machine whose answer is kept apart from the doctors' until the comparison.
- **Why not only CURRY:** CURRY stays the clinical tool and exchanges files
  with EpiAtlas. But a bias-free analysis must be transparent, and CURRY has
  no place for neuropsychology or semiology.
- **Science:** compare the machine and the doctors across many patients,
  against surgical outcome.
- **Needed from the team:** anonymised exports, the doctors' onset channels,
  and surgical outcomes.

Likely questions:
- *"Is it validated?"* Only on synthetic data so far. Validating it on real
  patients is the project.
- *"Does it replace the epileptologist?"* No. It is a second, independent
  reading.

## 11. Glossary

| Term | Meaning |
|---|---|
| **sEEG** | Depth electrodes in the brain, each with several contacts (A1, A2…). |
| **Montage** | How channels are referenced. Bipolar = neighbour minus neighbour. |
| **EI** | Epileptogenicity Index (section 6). |
| **Interictal spike** | A brief sharp discharge between seizures. |
| **NIfTI / DICOM** | Research / hospital image formats. DICOM is usually one file per slice. |
| **EDF** | The most common EEG/sEEG export format. |
| **Blind reading** | A reading made without seeing reports or other tests. |
| **Engel class** | Surgery outcome. I = seizure-free, II–IV = still has seizures. |
