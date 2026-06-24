# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- Duplicate-upload guard: re-dropping a file that was already uploaded to OSCAR
  no longer creates a second document. If the earlier run uploaded but never
  reached the copy-sheet (e.g. it errored at the Sheets step), re-dropping now
  writes the missing sheet row without contacting OSCAR — so re-dropping a file
  is the automatic way to recover a half-finished upload. The earlier
  completion meaning (incomplete / signature-missing) is preserved.

---

## [1.0.0] - 2026-06-15

### Added
- Full intake pipeline: PDF drop -> classify -> extract -> validate -> OSCAR search -> upload -> Sheets log
- Support for two questionnaire types: Adult ADHD Centre and ADHD Centre for Women
- Fillable PDF (AcroForm) extraction -- reads text fields directly without OCR
- OCR fallback via Tesseract for scanned / handwritten forms
- Consent page signature detection (ink-density + AcroForm field methods)
- Response-completeness validation on questionnaire pages 6-11 (Adult) / 6-12 (Women's)
  - Widget-first approach (exact, reads form fields); ink fallback for scanned forms
  - Operator Approve & Continue | Decline & Return to Patient gate
  - Auto-generated incomplete-sections follow-up email
- Patient search in OSCAR/KAI EMR:
  - Tier 1: exact Last + First + DOB
  - Tier 2: Email + DOB
  - Tier 3: DOB-range search; name-only matches surfaced to operator with DOB conflict note
- DOB write-back to OSCAR when operator confirms a name-matched chart with DOB difference
- OSCAR document upload (Type = Form, description = ADHD Assessment Tool)
- Chart sync: detects first/last/preferred-name discrepancies, offers operator update
- Local CSV copy-sheet for paste into master Google Sheet (configurable column layout)
- Google Sheets integration (PHI-safe -- no patient names)
- SQLite audit log (append-only) and processing record repository
- PySide6 GUI:
  - Drag-and-drop zone (from Outlook or Explorer)
  - Live activity log panel
  - Processed-patients table (Demographic No, Name, Email, Status) -- copyable
  - Session resume dialog on startup
  - Signature Missing filter toggle on the patient table
- Dashboard status colours: green (Completed), amber (Signature Missing), orange (warnings)
- Column A (MSP / Private) auto-populated from OSCAR Booking Alert keywords
- `config.yaml` driven by typed frozen dataclasses; no hardcoded paths or EMR URLs
- 95-test suite covering all business-rule gates

### Consent signature workflow
- Missing consent signature **does not block processing**
- Document is uploaded to OSCAR and logged regardless
- Status = `Completed - Signature Missing on Consent Form`
- Copy-ready follow-up email generated automatically for staff to send
- "Signature Missing" filter on dashboard for quick staff follow-up

### Security / PHI
- OSCAR credentials entered at runtime (never stored to disk)
- `config.yaml`, SQLite DB, logs, and PHI folders all git-ignored
- Google Sheets guard refuses any row containing a patient name
- Audit log records every pipeline transition for compliance

---

## Unreleased

### Pending live validation
- OSCAR/KAI Angular selectors (must be confirmed against live KAI screens)
- Booking Alert field name (JS tries common names; needs one live Private patient test)
- DOB write-back (coded; needs supervised test on a chart with a known DOB mismatch)
- Signature threshold calibration (use `tools/calibrate_signature.py`)
- Tesseract installation on clinic PC
- Google Sheets end-to-end with a real service account

### Planned
- Duplicate upload guard (re-dropping a completed file currently re-uploads it)
- Column A default value for non-private patients (currently blank; "MSP" TBD)
