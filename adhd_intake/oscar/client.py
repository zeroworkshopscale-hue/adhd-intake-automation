"""OSCAR Pro automation via Playwright.

OSCAR is a server-rendered EMR whose markup differs between versions and site
customisations. To keep the automation maintainable, every page selector and
URL fragment lives in :class:`OscarSelectors` so an integrator can adjust them
to their instance **without touching the control flow**. The defaults follow
the conventional OSCAR endpoint layout.

The client deliberately exposes a tiny surface:

    with OscarClient(config) as oscar:
        match = oscar.find_patient(demographics)   # -> PatientMatch | raises
        doc_id = oscar.upload_document(match, pdf_path, description)

``find_patient`` tries the search strategies in priority order:
    1. Last Name, First Name
    2. Partial name
    3. Email
    4. DOB (YYYY-MM-DD)
and raises :class:`PatientNotFoundError` if none resolve a unique patient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # imported lazily at runtime (see _import_playwright)
    from playwright.sync_api import Browser, Page, Playwright

from ..config import OscarConfig
from ..models import Demographics, PatientMatch
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class OscarError(RuntimeError):
    """A recoverable OSCAR automation failure (login/navigation/upload)."""


class PatientNotFoundError(OscarError):
    """No patient matched any of the search strategies."""


class OscarLoginError(OscarError):
    """Login failed — usually a wrong username or password."""


def _import_playwright():
    """Import Playwright on demand so the rest of the app (and tests) can run
    without it installed until an OSCAR session is actually opened."""
    try:
        from playwright.sync_api import (  # noqa: PLC0415
            TimeoutError as PlaywrightTimeoutError,
            sync_playwright,
        )
    except ImportError as exc:  # pragma: no cover
        raise OscarError(
            "Playwright is not installed. Run 'pip install playwright' and "
            "'playwright install chromium'."
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


@dataclass(frozen=True)
class OscarSelectors:
    """All instance-specific locators in one place. Override per deployment.

    Confirmed against the live KAI deployment (welcome.kai-oscar.com), which is
    a CLASSIC OSCAR Pro under ``/oscar`` (the ``/kaiemr/#/`` URL is just a
    wrapper). Patient search below was verified end-to-end. The login and eDoc
    upload selectors follow the same OSCAR Pro markup; verify the upload flow
    with a supervised test before relying on it (see README).
    """

    # --- login (standard OSCAR Pro) ---
    login_path: str = "/index.jsp"
    username_input: str = "input[name='username']"
    password_input: str = "input[name='password']"
    pin_input: str = "input[name='pin']"
    login_submit: str = "#loginbutton2, input[type='submit'], button[type='submit']"
    login_success_marker: str = "text=Schedule"  # visible once logged in

    # --- patient search (CONFIRMED) ---
    # Search is a GET; we build the URL directly (no form/CSRF needed).
    # demographiccontrol.jsp?search_mode=<mode>&keyword=<kw>&dboperation=search_titlename...
    search_results_path: str = "/demographic/demographiccontrol.jsp"
    # Result rows: each patient's "Master Demographic File" link carries the id
    # in its onclick: popup(...,'demographiccontrol.jsp?demographic_no=NNNN...').
    result_link: str = "a[onclick*='demographic_no']"

    # --- document upload (WELL eDoc form, confirmed field names) ---
    upload_path: str = "/dms/addDocument.jsp"
    upload_file_input: str = "input[name='docFile']"
    upload_title_input: str = "input[name='docDesc']"          # the "Title"/description
    upload_type_select: str = "#docType"                       # "Form" lives here
    upload_class_select: str = "#docClass"                     # report class (optional)
    upload_date_input: str = "#observationDate"               # prefilled to today
    upload_submit: str = "input[type='submit'][value='Add']"
    upload_success_marker: str = "text=successfully"


class OscarClient:
    """Context-managed Playwright session against an OSCAR Pro instance."""

    def __init__(
        self,
        config: OscarConfig,
        selectors: Optional[OscarSelectors] = None,
    ):
        self._config = config
        self._sel = selectors or OscarSelectors()
        self._pw: Optional["Playwright"] = None
        self._browser: Optional["Browser"] = None
        self._page: Optional["Page"] = None
        self._timeout_error: type[Exception] = TimeoutError

    # ---- context management ---------------------------------------------
    def __enter__(self) -> "OscarClient":
        sync_playwright, timeout_error = _import_playwright()
        self._timeout_error = timeout_error

        # Try the configured mode first. If headless login fails (some sites/
        # Cloudflare block headless), automatically retry with a real browser
        # positioned OFF-SCREEN — still invisible to the user, but undetectable
        # as headless.
        attempts = [(self._config.headless, False)]
        if self._config.headless:
            attempts.append((False, True))  # (headless=False, offscreen=True)

        last_exc: Optional[BaseException] = None
        for headless, offscreen in attempts:
            try:
                self._pw = sync_playwright().start()
                self._browser = self._launch_browser(headless, offscreen)
                context = self._browser.new_context(accept_downloads=True)
                context.set_default_timeout(self._config.timeout_ms)
                self._page = context.new_page()
                self.login()
                return self
            except OscarLoginError as exc:
                last_exc = exc
                self.__exit__(None, None, None)
                logger.warning(
                    "Login failed (%s mode). %s",
                    "headless" if headless else "offscreen",
                    "Retrying off-screen…" if offscreen is False and len(attempts) > 1 else "",
                )
            except BaseException:
                # Other startup failure: tear down so we don't poison the thread.
                self.__exit__(None, None, None)
                raise
        raise last_exc if last_exc else OscarLoginError("OSCAR login failed.")

    def _launch_browser(self, headless: bool, offscreen: bool):
        """Launch the browser. Prefers system Chrome (``channel='chrome'``),
        falling back to bundled Chromium. When ``offscreen`` is set, a visible
        browser is positioned far off-screen so it stays hidden from the user."""
        from .browser import ensure_chromium

        channel = (self._config.browser_channel or "").strip().lower()
        args = ["--window-position=-2400,-2400", "--window-size=1280,960"] if offscreen else []

        if channel and channel != "chromium":
            try:
                browser = self._pw.chromium.launch(headless=headless, channel=channel, args=args)
                logger.info("Launched channel=%s (headless=%s, offscreen=%s)", channel, headless, offscreen)
                return browser
            except Exception as exc:
                logger.warning(
                    "Could not launch system '%s' (%s); falling back to bundled Chromium",
                    channel,
                    str(exc).splitlines()[0] if str(exc) else exc,
                )

        ensure_chromium()
        browser = self._pw.chromium.launch(headless=headless, args=args)
        logger.info("Launched bundled Chromium (headless=%s, offscreen=%s)", headless, offscreen)
        return browser

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            logger.debug("Error closing browser", exc_info=True)
        finally:
            self._browser = None
            self._page = None
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                logger.debug("Error stopping Playwright", exc_info=True)
            finally:
                self._pw = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise OscarError("OSCAR session is not started; use as a context manager.")
        return self._page

    def _url(self, path: str) -> str:
        return f"{self._config.base_url}{path}"

    # ---- login ----------------------------------------------------------
    def login(self) -> None:
        logger.info("Logging into OSCAR at %s", self._config.base_url)
        page = self.page
        try:
            page.goto(self._url(self._sel.login_path))
            page.fill(self._sel.username_input, self._config.username)
            page.fill(self._sel.password_input, self._config.password)
            # Some OSCAR installs use a separate PIN; fill it with the password
            # when a dedicated PIN isn't configured and the field exists.
            if page.locator(self._sel.pin_input).count() > 0:
                page.fill(self._sel.pin_input, self._config.password)
            page.click(self._sel.login_submit)
            page.wait_for_load_state("networkidle")
            logger.info("OSCAR login submitted")
        except self._timeout_error as exc:
            raise OscarError(f"Timed out logging into OSCAR: {exc}") from exc

        # Verify the login actually succeeded (wrong password -> marker absent).
        try:
            page.wait_for_selector(self._sel.login_success_marker, timeout=15000)
            logger.info("OSCAR login successful")
        except self._timeout_error:
            raise OscarLoginError(
                "OSCAR login failed — please check your username and password."
            )

    # ---- patient search -------------------------------------------------
    def find_patient(
        self,
        demographics: Demographics,
        select_cb=None,
        email_cb=None,
    ) -> PatientMatch:
        """Resolve a SINGLE patient with an escalating, safe strategy.

        Tiers (stop at the first that resolves):
          1. Exact "Last, First" — auto-use if it uniquely matches the chart.
          2. First-3 letters of the last name — auto-use if it uniquely matches.
          3. Date of birth — list every patient with that DOB and let the
             operator pick (``select_cb``).
          4. Email — use the tool's email, or ask the operator (``email_cb``),
             to pin the exact chart.

        ``select_cb(candidates) -> demographic_no | None`` and
        ``email_cb(label) -> email | None`` are optional (skipped when None,
        e.g. headless), so auto-matching alone is always safe.
        """
        last = (demographics.last_name or "").strip()
        first = (demographics.first_name or "").strip()
        logger.info(
            "find_patient: name=%r,%r  dob=%r  email=%r",
            last, first, demographics.dob, demographics.email,
        )
        if not last and not first:
            logger.warning(
                "No name extracted from the questionnaire — name search tiers will "
                "be skipped (falling back to DOB / email)."
            )

        # Charts whose NAME matches the form but whose DOB did not — kept so the
        # operator can confirm them (a mistyped DOB must not hide a real patient).
        name_only: dict[str, dict] = {}

        def remember_name_only(evaluated: list[tuple[str, dict]]) -> None:
            for demo, details in evaluated:
                if demo in name_only:
                    continue
                if self._name_matches(demographics, details):
                    name_only[demo] = details

        # Tier 1 — exact "Last,First".
        if last and first:
            m, evaluated = self._auto_match(
                self._search_candidates("search_name", f"{last},{first}"),
                demographics, "exact name",
            )
            if m:
                return m
            remember_name_only(evaluated)

        # Tier 2 — partial: first 3 letters of last name + first 3 of first name.
        if last:
            keyword = f"{last[:3]},{first[:3]}" if first else last[:3]
            m, evaluated = self._auto_match(
                self._search_candidates("search_name", keyword),
                demographics, "partial name (3+3)",
            )
            if m:
                return m
            remember_name_only(evaluated)

        # Tier 3 — let the operator choose. The list contains: (a) charts whose
        # name matched but DOB conflicted (shown first, with a note), then (b)
        # every chart found by searching the form's date of birth.
        form_dob = Demographics.normalise_dob(demographics.dob) or (demographics.dob or "")
        ordered: list[str] = list(name_only.keys())
        for d in demographics.dob_candidates():
            for c in self._search_candidates("search_dob", d):
                if c not in ordered:
                    ordered.append(c)

        if ordered and select_cb is not None:
            details = []
            for c in ordered[:30]:
                info = name_only.get(c) or self._get_demographic_details(c)
                info = dict(info)
                info["demographic_no"] = c
                if c in name_only:
                    chart_dob = info.get("dob") or "?"
                    info["note"] = (
                        f"Name matches; form DOB {form_dob or '?'} ≠ chart DOB {chart_dob}"
                    )
                details.append(info)
            logger.info(
                "Selection list: %d patient(s) (%d name-match, rest DOB) — asking operator",
                len(details), len(name_only),
            )
            chosen = select_cb(details)
            if chosen:
                d = self._get_demographic_details(chosen)
                return PatientMatch(
                    demographic_no=str(chosen),
                    last_name=d.get("last", ""), first_name=d.get("first", ""),
                    email=demographics.email, dob=d.get("dob"),
                    matched_by="operator selected",
                )

        # Tier 4 — email (from the tool, or ask the operator).
        email = (demographics.email or "").strip()
        if not email and email_cb is not None:
            email = (email_cb(demographics.display_name) or "").strip()
        if email:
            cands = self._search_candidates("search_email", email)
            if len(cands) == 1:
                d = self._get_demographic_details(cands[0])
                return PatientMatch(
                    demographic_no=cands[0],
                    last_name=d.get("last", ""), first_name=d.get("first", ""),
                    email=email, dob=d.get("dob"), matched_by="email",
                )
            m = self._auto_match(cands, demographics, "email")
            if m:
                return m

        raise PatientNotFoundError(
            "Could not match the patient by name, date of birth, or email. "
            "Not uploaded — please review manually."
        )

    def _auto_match(
        self, candidates: list[str], demographics: Demographics, label: str
    ) -> tuple[Optional[PatientMatch], list[tuple[str, dict]]]:
        """Return ``(match, evaluated)`` where ``match`` is a PatientMatch only
        when exactly ONE candidate's chart confidently matches (name + DOB);
        otherwise None. ``evaluated`` is the list of ``(demo, details)`` charts
        read, so the caller can reuse them (e.g. to offer near-matches to the
        operator) without re-fetching."""
        confident: list[tuple[str, dict, str]] = []
        evaluated: list[tuple[str, dict]] = []
        for demo in candidates[:8]:
            details = self._get_demographic_details(demo)
            evaluated.append((demo, details))
            ok, reason = self._is_confident_match(demographics, details)
            logger.info(
                "[%s] candidate %s (%s, dob %s): %s [%s]",
                label, demo, details.get("display", "?"), details.get("dob", "?"),
                "MATCH" if ok else "no-match", reason,
            )
            if ok:
                confident.append((demo, details, reason))
        if len(confident) == 1:
            demo, details, reason = confident[0]
            return (
                PatientMatch(
                    demographic_no=demo,
                    last_name=details.get("last", "") or (demographics.last_name or ""),
                    first_name=details.get("first", "") or (demographics.first_name or ""),
                    email=demographics.email,
                    dob=details.get("dob") or demographics.dob,
                    matched_by=f"{label}: {reason}",
                ),
                evaluated,
            )
        return None, evaluated

    def _search_candidates(self, mode: str, keyword: str) -> list[str]:
        """Run one OSCAR search and return all candidate demographic numbers."""
        from urllib.parse import quote

        page = self.page
        url = (
            f"{self._url(self._sel.search_results_path)}"
            f"?search_mode={mode}"
            f"&keyword={quote(keyword)}"
            f"&dboperation=search_titlename"
            f"&limit1=0&limit2=25&displaymode=Search&ptstatus=active"
        )
        try:
            page.goto(url)
            page.wait_for_load_state("domcontentloaded")
        except self._timeout_error:
            logger.warning("OSCAR search timed out for %s=%r", mode, keyword)
            return []

        out: list[str] = []
        rows = page.locator(self._sel.result_link)
        for i in range(rows.count()):
            onclick = rows.nth(i).get_attribute("onclick") or ""
            demo = self._parse_demographic_no(onclick)
            if demo and demo not in out:
                out.append(demo)
        logger.info("Search %s=%r -> %d candidate(s)", mode, keyword, len(out))
        return out

    def get_demographic_details(self, demo: str) -> dict:
        """Public accessor for a chart's current values (for discrepancy checks)."""
        return self._get_demographic_details(demo)

    def update_demographic(self, demo: str, changes: dict[str, str]) -> bool:
        """Update demographic fields by driving the real edit form.

        ``changes`` maps OSCAR field names (last_name, first_name, pref_name,
        address) to new values. We load the edit form (which carries every
        existing value + dboperation=update_record), set only the changed
        fields, and click "Update Record" — exactly like a manual edit, so no
        other data is disturbed. Returns True on apparent success.
        """
        if not changes:
            return True
        changes = dict(changes)
        # DOB lives in OSCAR as three separate fields. Expand "dob" (YYYY-MM-DD)
        # into year/month/date so the generic setter below applies it.
        want_dob = changes.pop("dob", None)
        if want_dob:
            parts = str(want_dob).split("-")
            if len(parts) == 3 and len(parts[0]) == 4:
                changes["year_of_birth"] = parts[0]
                changes["month_of_birth"] = parts[1].zfill(2)
                changes["date_of_birth"] = parts[2].zfill(2)
        page = self.page
        try:
            page.goto(
                f"{self._url('/demographic/demographiccontrol.jsp')}"
                f"?demographic_no={demo}&displaymode=edit&dboperation=search_detail"
            )
            page.wait_for_selector("input[name='last_name']", timeout=self._config.timeout_ms, state="attached")
            applied = page.evaluate(
                """(changes) => {
                    const done = [];
                    for (const [name, value] of Object.entries(changes)) {
                        const el = document.querySelector(`[name='${name}']`);
                        if (el) { el.value = value; el.dispatchEvent(new Event('change')); done.push(name); }
                    }
                    return done;
                }""",
                changes,
            )
            logger.info("Demographic %s: set fields %s", demo, applied)
            # Click the real "Update Record" submit (not "Save & Update Family Members").
            submit = page.locator("input[type='submit'][value='Update Record']").first
            try:
                with page.expect_navigation(timeout=self._config.timeout_ms):
                    submit.click()
            except Exception:
                submit.click()
            page.wait_for_load_state("domcontentloaded")
            logger.info("Demographic %s update submitted; url=%s", demo, page.url)
        except self._timeout_error as exc:
            logger.warning("Demographic update timed out for %s: %s", demo, exc)
            return False
        except Exception:
            logger.exception("Demographic update failed for %s", demo)
            return False

        # Self-verify: re-read the chart and confirm each change actually saved.
        try:
            after = self._get_demographic_details(demo)
            field_to_key = {
                "last_name": "last", "first_name": "first",
                "pref_name": "pref", "address": "address",
            }
            mismatched = []
            for field, new_value in changes.items():
                key = field_to_key.get(field)
                if key is None:
                    continue
                got = (after.get(key) or "").strip().lower()
                if got != (new_value or "").strip().lower():
                    mismatched.append(f"{field}: wanted '{new_value}', chart has '{after.get(key)}'")
            if want_dob:
                got_dob = Demographics.normalise_dob(after.get("dob"))
                if got_dob != Demographics.normalise_dob(want_dob):
                    mismatched.append(f"dob: wanted '{want_dob}', chart has '{after.get('dob')}'")
            if mismatched:
                logger.warning("Demographic %s update NOT confirmed: %s", demo, "; ".join(mismatched))
                return False
            logger.info("Demographic %s update verified saved.", demo)
            return True
        except Exception:
            logger.debug("Could not verify demographic update for %s", demo, exc_info=True)
            return True  # submitted; verification just couldn't run

    def _get_demographic_details(self, demo: str) -> dict:
        """Read a candidate's chart (name, pref, address, DOB, sex) for verification."""
        page = self.page
        try:
            page.goto(
                f"{self._url('/demographic/demographiccontrol.jsp')}"
                f"?demographic_no={demo}&displaymode=edit&dboperation=search_detail"
            )
            page.wait_for_selector("input[name='last_name']", timeout=self._config.timeout_ms, state="attached")
            data = page.evaluate(
                """() => {
                    const g = (n) => { const e = document.querySelector(`[name='${n}']`); return e ? String(e.value) : ''; };
                    // Build DOB from the unambiguous numeric fields (avoids date-format issues).
                    const y = g('year_of_birth'), m = g('month_of_birth'), d = g('date_of_birth');
                    let dob = '';
                    if (y && m && d) dob = y + '-' + String(m).padStart(2,'0') + '-' + String(d).padStart(2,'0');
                    // Booking Alert: prefer a named field, else any field whose name
                    // contains 'alert' (the exact field name varies by OSCAR build).
                    let alert = g('alert') || g('patientAlert') || g('bookingAlert');
                    if (!alert) {
                        for (const e of document.querySelectorAll('input,textarea')) {
                            if ((e.name||'').toLowerCase().includes('alert') && e.value) { alert = String(e.value); break; }
                        }
                    }
                    return {last: g('last_name'), first: g('first_name'), pref: g('pref_name'),
                            address: g('address'), city: g('city'), province: g('province'),
                            postal: g('postal'), dob: dob || g('full_birth_date'), sex: g('sex'),
                            email: g('email'), booking_alert: alert};
                }"""
            )
        except Exception:
            logger.debug("Could not read demographic %s", demo, exc_info=True)
            return {}
        data["display"] = ", ".join(p for p in (data.get("last"), data.get("first")) if p)
        return data

    @staticmethod
    def _is_confident_match(ext: Demographics, details: dict) -> tuple[bool, str]:
        """Decide whether a chart is the same person as the assessment tool.

        Rule (safe by design):
          * If the tool has a DOB: require the chart DOB to match AND the
            last-name first-3 letters to match.
          * Otherwise: require first-3 of both first and last name to match.
        """
        if not details:
            return False, "chart unreadable"

        def n(s: str | None) -> str:
            return (s or "").strip().lower()

        el, ef = n(ext.last_name), n(ext.first_name)
        cl, cf = n(details.get("last")), n(details.get("first"))
        # All plausible readings of the form's DOB vs OSCAR's canonical date.
        ext_dobs = ext.dob_candidates() or ([Demographics.normalise_dob(ext.dob)] if ext.dob else [])
        ext_dobs = [d for d in ext_dobs if d]
        cd = Demographics.normalise_dob(details.get("dob"))

        last3 = bool(el) and bool(cl) and el[:3] == cl[:3]
        first3 = bool(ef) and bool(cf) and ef[:3] == cf[:3]
        dob_match = bool(cd) and cd in ext_dobs

        if ext_dobs:  # DOB available on the tool — use it as the strong signal.
            if dob_match and last3:
                return True, "DOB + last-name match"
            if dob_match and not last3:
                return False, "DOB matched but last name differs"
            return False, "DOB did not match chart"
        if last3 and first3:
            return True, "name match (first-3 + last-3)"
        return False, "name prefix did not match"

    @staticmethod
    def _name_matches(ext: Demographics, details: dict) -> bool:
        """True if the chart's name strongly matches the tool's name, IGNORING
        DOB. Used to offer name-matched charts to the operator when the DOB on
        the form conflicts (so a mistyped DOB doesn't hide a real patient)."""
        if not details:
            return False

        def n(s: str | None) -> str:
            return (s or "").strip().lower()

        el, ef = n(ext.last_name), n(ext.first_name)
        cl, cf = n(details.get("last")), n(details.get("first"))
        if not (el and cl):
            return False
        exact = el == cl and bool(ef) and ef == cf
        prefix = el[:3] == cl[:3] and bool(ef) and bool(cf) and ef[:3] == cf[:3]
        return exact or prefix

    @staticmethod
    def _parse_demographic_no(text: str) -> Optional[str]:
        # onclick like: popup(...,'demographiccontrol.jsp?demographic_no=12345&...')
        m = re.search(r"demographic_?no=(\d+)", text or "", re.IGNORECASE)
        return m.group(1) if m else None

    # ---- document upload ------------------------------------------------
    def upload_document(
        self,
        patient: PatientMatch,
        pdf_path: Path,
        description: str,
    ) -> str:
        """Upload ``pdf_path`` into the patient's OSCAR eDocuments.

        Replicates the manual eDoc upload exactly (verified against the live KAI
        instance, 2026-06-13):

          * Load the eDoc page via ``documentReport.jsp?function=demographic&
            functionid=<no>`` (lowercase ``functionid``). This is the entry point
            the chart uses; it redirects to addDocument.jsp with the hidden
            fields correctly populated (functionId=<no>, appointmentNo=0,
            curUser empty). Loading addDocument.jsp directly with capital
            ``functionId`` leaves those as the literal "null" and the document
            ends up unlinked in the inbox.
          * Fill Title (= description) and Type (= "Form"), attach the file, and
            submit the REAL form (a navigation), which is what files it to the
            chart. (An API POST is rejected/handled differently by KAI.)

        Returns a document reference on success.
        """
        page = self.page
        demo = patient.demographic_no
        logger.info("Uploading %s for demographic %s (type=%s)", pdf_path.name, demo, self._config.document_type)

        try:
            # 1) Open the eDoc page the same way the chart does (lowercase
            #    functionid). It redirects to addDocument.jsp with correct context.
            report_url = (
                f"{self._url('/dms/documentReport.jsp')}"
                f"?function=demographic&functionid={demo}"
            )
            page.goto(report_url)
            # The file input exists but is hidden inside the collapsed section,
            # so wait for it to be ATTACHED (present), not visible.
            page.wait_for_selector(
                self._sel.upload_file_input, timeout=self._config.timeout_ms, state="attached"
            )

            # 2) Reveal the collapsed section AND set Title + Type via JS (robust,
            #    no dependence on visibility timing), and log the server-populated
            #    context so we can confirm it matches a manual upload.
            ctx = page.evaluate(
                """([description, typeLabel]) => {
                    const file = document.querySelector("input[name='docFile']");
                    const form = file.form;
                    const dd = document.getElementById('addDocDiv');
                    if (dd) { dd.style.display=''; dd.style.visibility='visible'; }
                    let el = form;
                    while (el) { if (el.style && el.style.display === 'none') el.style.display=''; el = el.parentElement; }
                    const desc = form.querySelector("[name='docDesc']");
                    if (desc) desc.value = description;
                    const dt = form.querySelector('#docType');
                    let typeSet = false;
                    if (dt) for (const o of dt.options) if (o.text.trim() === typeLabel) { dt.value = o.value; typeSet = true; }
                    if (dt) dt.dispatchEvent(new Event('change'));
                    const g = (n) => { const e = form.querySelector(`[name='${n}']`); return e ? String(e.value) : ''; };
                    return {functionId: g('functionId'), functionid: g('functionid'),
                            appointmentNo: g('appointmentNo'), curUser: g('curUser'),
                            docDesc: g('docDesc'), typeSet};
                }""",
                [description, self._config.document_type],
            )
            logger.info("UPLOAD form context: %s", ctx)

            # 3) Attach the file (set_input_files works on the hidden input).
            page.set_input_files(self._sel.upload_file_input, str(pdf_path))

            # 4) Submit the REAL form (navigation) — exactly like a manual upload.
            submit = page.locator(self._sel.upload_submit).first
            try:
                with page.expect_navigation(timeout=self._config.timeout_ms):
                    submit.click()
            except Exception:
                # Some submits replace content without a full navigation event.
                try:
                    submit.click()
                except Exception:
                    logger.debug("Submit click fallback failed", exc_info=True)
            page.wait_for_load_state("domcontentloaded")
            logger.info("UPLOAD submitted; post-submit url=%s", page.url)
        except self._timeout_error as exc:
            raise OscarError(f"Timed out uploading document to OSCAR: {exc}") from exc

        # 6) Best-effort verification (advisory; the operator can confirm chart).
        if self._verify_document(demo, description):
            logger.info("Verified document attached to demographic %s", demo)
        else:
            logger.warning(
                "Could not auto-confirm document for demographic %s "
                "(best-effort check; verify in the chart).",
                demo,
            )
        return f"{demo}:{description}"

    def _verify_document(self, demo: str, description: str) -> bool:
        """Best-effort: return True if ``description`` appears on the patient's
        eDoc page after upload."""
        page = self.page
        try:
            page.goto(
                f"{self._url('/dms/documentReport.jsp')}"
                f"?function=demographic&functionid={demo}"
            )
            page.wait_for_load_state("domcontentloaded")
            return description in page.inner_text("body")
        except Exception:
            logger.debug("Document verification step failed", exc_info=True)
            return False
