# Project Status — ADHD Intake Automation

**As of:** 2026-06-15  
**Version:** 1.0.0  
**Test suite:** 95 / 95 passing  
**Build:** SELFTEST OK

---

## Completed modules

| Module | Location | Status |
|--------|----------|--------|
| Configuration system | `adhd_intake/config.py` | Done -- typed frozen dataclasses, YAML-loaded |
| Domain models | `adhd_intake/models.py` | Done -- dataclasses, enums, DOB parsing |
| SQLite database | `adhd_intake/database/` | Done -- repository, append-only audit log |
| PDF classification | `adhd_intake/extraction/classifier.py` | Done -- fillable vs. scanned detection |
| Data extraction | `adhd_intake/extraction/extractor.py` | Done -- AcroForm fields, text layer, OCR fallback |
| Questionnaire templates | `adhd_intake/extraction/templates.py` | Done -- Adult + Women's tool definitions |
| OCR engine | `adhd_intake/ocr/engine.py` | Done -- Tesseract wrapper (needs Tesseract installed) |
| Signature validation | `adhd_intake/validation/signature.py` | Done -- ink-density + AcroForm methods |
| Completeness validation | `adhd_intake/validation/completeness.py` | Done -- widget-first + ink fallback |
| Pipeline orchestration | `adhd_intake/pipeline/processor.py` | Done -- all gates, session state |
| Services / composition root | `adhd_intake/services.py` | Done -- wires all components lazily |
| Local CSV copy-sheet | `adhd_intake/sheets/local_sheet.py` | Done -- configurable column layout |
| Google Sheets client | `adhd_intake/sheets/client.py` | Done -- PHI guard, auto-header |
| PySide6 dashboard | `adhd_intake/dashboard/` | Done -- drag-drop, worker thread, table, dialogs |
| OSCAR client (structure) | `adhd_intake/oscar/client.py` | Done -- search tiers, upload, chart sync |
| Session resume | `adhd_intake/services.py` | Done -- session_state.json, begin_session() |
| Program status (col A) | `adhd_intake/config.py` | Done -- ProgramConfig.classify() from Booking Alert |
| Test suite | `tests/` | Done -- 95 tests, no heavy deps required |
| PyInstaller build | `ADHD_Intake.spec` | Done -- single-folder exe, selftest passes |

---

## Incomplete modules / features

| Feature | Status | Blocker |
|---------|--------|---------|
| OSCAR/KAI Angular selectors | Not verified | Must confirm against live KAI screens; selectors in `oscar/client.py` are placeholders |
| Automated OSCAR upload | Not live-tested | Depends on confirmed selectors above |
| DOB write-back to OSCAR | Coded, not live-tested | Month/day field format in KAI edit form unconfirmed; needs one supervised test |
| Preferred-name update | Coded, not live-tested | Needs a live chart with a name mismatch |
| Booking Alert field name | Coded, unverified | JS tries common names; confirm "Chart program status=..." in logs with a real Private patient |
| Google Sheets cloud sync | Coded, not end-to-end tested | Needs real service account + correct spreadsheet ID |
| Duplicate upload guard | Done | Re-dropping an already-uploaded file skips OSCAR and recovers the missing sheet row (`pipeline/processor.py`, `tests/test_duplicate_guard.py`) |

---

## Known issues

| Issue | Severity | Notes |
|-------|----------|-------|
| OSCAR selectors unconfirmed | High | App will fail at the OSCAR step until selectors are validated against live KAI. Logged gracefully as "OSCAR error"; no data loss. |
| Signature threshold not calibrated | Medium | `min_ink_density: 0.05` is a reasonable estimate. Run `tools/calibrate_signature.py` with real signed + unsigned forms before going live. |
| Tesseract unconfirmed on clinic PC | Medium | Scanned/handwritten forms will not OCR until Tesseract-OCR is installed. Fillable PDFs (all current forms) are unaffected. |
| Column A default value undefined | Low | When no private keywords are found in the Booking Alert, column A is blank. Whether it should default to "MSP" for non-private patients is undecided. |
| Duplicate upload not blocked | Resolved | Re-dropping an already-uploaded file now skips the OSCAR upload and, if needed, writes the missing sheet row. |

---

## Next priorities (in order)

1. **Confirm OSCAR/KAI selectors** -- open DevTools on the live KAI instance, copy the actual CSS selectors / Angular routes, update `OscarSelectors` in `adhd_intake/oscar/client.py`, and test end-to-end with a non-patient test account.

2. **Run signature calibration** -- run `tools/calibrate_signature.py --signed signed_sample.pdf --unsigned blank_sample.pdf` and update `validation.min_ink_density` in `config.yaml`.

3. **Verify Booking Alert field name** -- process one real Private patient and confirm "Chart program status='Private'" appears in the log. If not, check DevTools for the actual field name and update the JS in `_get_demographic_details`.

4. **Live test DOB write-back** -- arrange a chart with a known DOB mismatch, let the operator confirm, and verify OSCAR updates. Watch for the self-verify warning in logs.

5. **Confirm Tesseract installed** -- run `tesseract --version` on the clinic PC; if absent, install Tesseract-OCR for Windows.

6. **End-to-end Sheets test** -- configure a real service account, set `sheets.mode: google` in a test config, process one form, and confirm the row appears in the spreadsheet.

7. **Duplicate upload guard** -- add a check in `processor.py` that refuses to upload if the record hash already has a successful OSCAR document ID.

8. **Column A default value** -- decide: should non-private patients show "MSP" or be left blank for manual entry? Update `ProgramConfig.default_label` and `config.example.yaml` once decided.

---

## Architecture decisions (rationale)

| Decision | Reason |
|----------|--------|
| Missing signature does not block upload | Clinic decided forms should be processed regardless; staff follow up via email to collect consent. Status = `Completed - Signature Missing` makes follow-up visible. |
| Signatures checked with widget-first + ink fallback | Current forms are fillable PDFs (AcroForm text fields). Ink-only detection caused false positives on blank forms. Widget reading is exact; ink fallback covers scanned/printed forms. |
| OSCAR credentials entered at runtime | OSCAR user accounts are personal; credentials must not be shared or stored. The login dialog captures them per-session only. |
| PHI guard in Sheets client | Belt-and-suspenders: even if a future code change accidentally passes names, the guard raises before the API call. |
| Local CSV + Google Sheets dual mode | Clinic currently uses a local paste workflow. Cloud Sheets is available for when they want direct sync, with the PHI guard ensuring no names leak. |
