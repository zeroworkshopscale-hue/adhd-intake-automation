# ADHD Intake Automation

A Windows desktop application (Python + PySide6) that automates the intake
of ADHD assessment questionnaires at the Adult ADHD Centre. Drag a
questionnaire PDF onto the dashboard and the app classifies it, validates
consent, extracts the data, finds the patient in **OSCAR/KAI EMR**, uploads the
document, logs a **PHI-safe** row to Google Sheets, and records everything in
SQLite with a full audit trail.

> **Supported assessment tools:** Adult ADHD Centre · ADHD Centre for Women

---

## Workflow

```
Drag PDF onto dashboard (from Outlook or file system)
        |
        v
Classify  -- fillable? --> read AcroForm fields / text layer
   |        scanned?  --> OCR (Tesseract)
   v
Extract demographics + questionnaire type
   |
   v
Check consent page for signature / initials
   |-- SIGNED    -> continue normally  -> status: Completed
   |-- MISSING   -> continue + flag    -> status: Completed - Signature Missing
   |                generate copy-ready follow-up email for staff to send
   v
Check response completeness (pages 6-11 / 6-12)
   |-- COMPLETE  -> continue
   |-- INCOMPLETE -> operator: Approve & Continue | Decline & Return to Patient
   v
Find patient in OSCAR (Last, First -> Email -> DOB; name-match + DOB mismatch
   |                   surfaced to operator for confirmation)
   |-- Not found  -> STOP: Patient Not Found (no upload, no Sheets)
   v
Upload to OSCAR Documents  (Type = Form)
   |
   v
Update copy-sheet (local CSV) and/or Google Sheets  [no names ever written]
   |
   v
COMPLETED -> move to processed/ -> appear in dashboard table
```

**Key principle:** a missing consent signature never blocks upload. The form is
processed and logged so the patient's OSCAR chart is complete; staff use the
generated email to collect consent separately.

Every step is written to an append-only **audit log** (SQLite) and the record
is persisted after each transition so a crash at any stage leaves a recoverable
trail.

---

## Architecture

```
adhd_intake/
+-- config.py            Typed config loaded from config.yaml
+-- models.py            Shared dataclasses & enums (status, demographics, ...)
+-- services.py          Composition root -- wires everything together
+-- database/            SQLite: connection, repository, audit log
+-- extraction/          Fillable-vs-scanned classification + data extraction
+-- ocr/                 Tesseract OCR for scanned / handwritten pages
+-- validation/          Consent signature detection + response completeness check
+-- oscar/               OSCAR Pro / KAI automation (Playwright): search + upload
+-- sheets/              Google Sheets logging (PHI-safe -- no names)
+-- pipeline/            Orchestration + business-rule gates
+-- dashboard/           PySide6 GUI: drag-drop zone, worker thread, table
+-- utils/               Logging, hashing, safe file moves
main.py                  Entry point
tests/                   95 business-rule tests (run on any machine, no EMR needed)
tools/                   calibrate_signature.py helper
```

The pipeline depends on OSCAR/Sheets/extraction/validation only through small
interfaces and lazy factories, so heavy native dependencies (PyMuPDF, Tesseract,
Playwright, gspread) load only when actually needed and the core logic is
unit-testable everywhere.

---

## Quick start

### 1. Python dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

### 2. Tesseract OCR (scanned/handwritten PDFs only)

Install **Tesseract-OCR for Windows** and note the path to `tesseract.exe`
(default: `C:\Program Files\Tesseract-OCR\tesseract.exe`). Typed/fillable
PDFs work without Tesseract.

### 3. Configuration

```powershell
Copy-Item config.example.yaml config.yaml
```

Edit `config.yaml`:

| Key | Description |
|-----|-------------|
| `oscar.base_url` | KAI EMR base URL (preset to `https://welcome.kai-oscar.com/oscar`) |
| `oscar.headless` | `true` = browser runs in background; `false` = visible (use for first runs) |
| `sheets.mode` | `local` (default), `google`, or `both` |
| `sheets.spreadsheet_id` | Google Sheet ID (only for `google` / `both` mode) |
| `validation.min_ink_density` | Calibrate with `tools/calibrate_signature.py` |
| `program.private_keywords` | Keywords in OSCAR Booking Alert that set column A = "Private" |

`config.yaml`, the SQLite database, logs, and PHI folders are all git-ignored.

### 4. Launch

**Easiest:** double-click `Launch ADHD Intake.bat`.

From a terminal:
```powershell
python main.py
python main.py --config other_config.yaml
```

The app asks for your **OSCAR username and password** at start-up. These are
kept in memory for the session only and are never written to disk.

### 5. Google Sheets service account (optional)

If using `sheets.mode: google` or `both`:

1. Create a Google Cloud service account and download its JSON key.
2. Place the key at `secrets/google_service_account.json` (git-ignored).
3. Share the target spreadsheet with the service-account email as Editor.
4. Set `sheets.spreadsheet_id` in `config.yaml`.

---

## Running tests

```powershell
python -m pytest
```

The 95-test suite covers all business-rule gates using injected fakes — no
OSCAR connection, Google account, Tesseract, or PyMuPDF required:

- Missing signature -> still uploads, status = `Completed - Signature Missing`
- Patient not found -> stops, no upload, no Sheets write
- Happy path -> upload + Sheets row + moved to `processed/`
- Incomplete questionnaire -> operator approve/decline gate
- Sheets rows never contain patient names (defensive guard)
- DOB normalisation, name-matching tiers, session resume, chart sync

---

## Dashboard features

| Feature | Details |
|---------|---------|
| Drag-and-drop | Drop PDFs from Outlook or Explorer onto the upload zone |
| Activity log | Live per-file progress shown in the right panel |
| Patient table | Demographic No, Name, Email, Status — copyable (Ctrl+C / right-click) |
| Signature Missing filter | Toggle button above the table to show only patients needing consent follow-up |
| Open copy sheet | Opens the local CSV in Excel for paste into the master Google Sheet |
| Session resume | On next launch: resume previous session (keeps table + CSV) or start fresh |

---

## Data safety

- **PHI never leaves the clinic via Sheets** -- only demographic number, email,
  pronoun, and processing metadata are logged.
- All patient detail (names, DOB) stays in the local SQLite database and the
  on-disk `processed/` folder.
- The audit log is append-only (never updated or deleted in normal operation).
- `config.yaml` and the service-account key are git-ignored; restrict filesystem
  permissions on the `data/` and `secrets/` folders.

---

## Known limitations / live-test items

- **OSCAR/KAI selectors** -- the Angular SPA selectors in `adhd_intake/oscar/client.py`
  must be confirmed against the live KAI screens before automated upload will work.
  Keep `oscar.headless: false` for initial testing.
- **Booking Alert field name** -- the JS reader tries common field names; confirm
  "Chart program status=..." appears in logs for a real Private patient.
- **Signature threshold** -- `min_ink_density: 0.05` is estimated; calibrate with
  `tools/calibrate_signature.py` using real signed and unsigned samples.
- **Tesseract** -- only needed for scanned forms; confirm installed on the clinic PC.
- **Google Sheets sync** -- wired but not tested end-to-end with a real service account.
