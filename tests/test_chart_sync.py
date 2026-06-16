"""Tests for chart discrepancy detection (drives the Alert dialog)."""

from __future__ import annotations

from adhd_intake.models import Demographics
from adhd_intake.pipeline.processor import IntakeProcessor

detect = IntakeProcessor._detect_discrepancies


def test_no_discrepancy_when_equal():
    tool = Demographics(first_name="Julia", last_name="Cillo")
    chart = {"first": "Julia", "last": "Cillo", "pref": "", "address": ""}
    assert detect(tool, chart) == []


def test_case_insensitive_no_discrepancy():
    tool = Demographics(first_name="julia", last_name="CILLO")
    chart = {"first": "Julia", "last": "Cillo", "pref": "", "address": ""}
    assert detect(tool, chart) == []


def test_last_name_difference_flagged():
    tool = Demographics(first_name="Julia", last_name="Cillo-Rose")
    chart = {"first": "Julia", "last": "Cillo", "pref": "", "address": ""}
    diffs = detect(tool, chart)
    assert len(diffs) == 1
    assert diffs[0].oscar_field == "last_name"
    assert diffs[0].tool_value == "Cillo-Rose"
    assert diffs[0].oscar_value == "Cillo"


def test_address_is_never_flagged():
    # OSCAR is the source of truth for address; it is never updated from the form.
    tool = Demographics(first_name="Julia", last_name="Cillo", address="9 Oak Ave")
    chart = {"first": "Julia", "last": "Cillo", "pref": "", "address": "123 Main St"}
    assert detect(tool, chart) == []


def test_preferred_name_flagged():
    tool = Demographics(first_name="Julia", last_name="Cillo", pref_name="Jules")
    chart = {"first": "Julia", "last": "Cillo", "pref": "", "address": ""}
    diffs = detect(tool, chart)
    assert len(diffs) == 1 and diffs[0].oscar_field == "pref_name"


def test_dob_difference_flagged():
    # Name matches but DOB differs -> offered as a correctable discrepancy.
    tool = Demographics(first_name="Dhirender", last_name="Test", dob="1999-11-12")
    chart = {"first": "Dhirender", "last": "Test", "pref": "", "dob": "1999-11-11"}
    diffs = detect(tool, chart)
    assert len(diffs) == 1
    assert diffs[0].oscar_field == "dob"
    assert diffs[0].tool_value == "1999-11-12"
    assert diffs[0].oscar_value == "1999-11-11"


def test_dob_equal_not_flagged():
    tool = Demographics(first_name="Dhirender", last_name="Test", dob="1999-11-11")
    chart = {"first": "Dhirender", "last": "Test", "pref": "", "dob": "1999-11-11"}
    assert detect(tool, chart) == []
