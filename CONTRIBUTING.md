# Contributing

This is an internal clinic tool. Only authorised Adult ADHD Centre staff and
contracted developers should make changes to this codebase.

---

## Development setup

```powershell
# Clone and install
git clone git@github.com:adhd-centre/adhd-intake-automation.git
cd adhd-intake-automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium

# Copy and edit config
Copy-Item config.example.yaml config.yaml
# Set oscar.base_url, sheets settings, etc.
```

---

## Running tests before any change

```powershell
python -m pytest
```

All 95 tests must pass. The suite runs on any machine with Python 3.11+ and
does not need a live OSCAR connection, Tesseract, or Google account.

---

## Branch conventions

| Branch | Purpose |
|--------|---------|
| `main` | Stable, clinic-ready code |
| `dev` | Integration branch for in-progress features |
| `fix/<short-description>` | Bug fixes |
| `feat/<short-description>` | New features |

---

## Commit message style

```
<type>: <short imperative description>  (max 72 chars)

Optional body: explain the WHY, not the WHAT. Reference issue numbers.
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

---

## PHI / safety rules

- **Never commit patient data.** All PHI is in `data/` and `logs/` (git-ignored).
- **Never commit credentials.** OSCAR password is entered at runtime; Google
  service-account key lives in `secrets/` (git-ignored).
- **Never commit `config.yaml`** -- it may contain the EMR URL and Sheets IDs.
- Before adding any new EMR interaction, test with a **non-patient test account**
  and keep `oscar.headless: false` to watch the browser.

---

## Adding a new sheet column

1. Add a resolver to `FIELD_RESOLVERS` in `adhd_intake/sheets/local_sheet.py`.
2. Add a `columns` entry in `config.yaml` (and in `config.example.yaml`).
3. No pipeline code changes needed.

---

## Changing OSCAR selectors

All KAI EMR selectors and route hashes are in `OscarSelectors` at the top of
`adhd_intake/oscar/client.py`. Update the constants there -- do not scatter
selector strings through the browser methods.

---

## Building the Windows executable

```powershell
python -m PyInstaller ADHD_Intake.spec --noconfirm
# Executable appears in dist/ADHD_Intake/ADHD_Intake.exe
```

Run the selftest before distributing:
```powershell
.\dist\ADHD_Intake\ADHD_Intake.exe --selftest
# Expected: SELFTEST OK  EXIT:0
```
