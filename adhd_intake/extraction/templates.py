"""Questionnaire templates.

Each supported assessment tool declares:

  * ``identifiers`` — substrings that, when found in the document text, identify
    the questionnaire type.
  * ``demographic_fields`` — a mapping of our canonical demographic keys to the
    list of possible AcroForm field names / text labels used on that form. The
    lists are tried in order, so put the most specific label first.

Add a new tool by appending a :class:`QuestionnaireTemplate` to ``TEMPLATES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import QuestionnaireType


@dataclass(frozen=True)
class QuestionnaireTemplate:
    type: QuestionnaireType
    identifiers: tuple[str, ...]
    # canonical key -> candidate labels / form-field names (lower-cased compare)
    demographic_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # canonical answer key -> candidate field names
    answer_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # result_key -> anchor phrase. If a checkmark/ink is found next to the
    # anchor on the page, answers[result_key] = "YES".
    checkbox_fields: dict[str, str] = field(default_factory=dict)
    # 1-based, inclusive page range whose question rows must each carry a
    # response (the rating-scale / yes-no sections). () disables the check.
    validation_pages: tuple[int, int] = ()
    # Candidate response-column header label *sets*, tried in order. The first
    # set that locates >= 2 columns on a page defines that page's grid. Using
    # whole phrases (not single words) avoids splitting one column into several.
    response_label_sets: tuple[tuple[str, ...], ...] = (
        ("Never or Rarely", "Sometimes", "Often or Very Often"),
        ("Never", "Rarely", "Sometimes", "Often", "Very Often"),
        ("Yes", "No"),
    )


# Checkbox-style consent questions: marked anywhere -> "YES".
_COMMON_CHECKBOXES = {
    "future_initiatives": "future ADHD initiatives",
    "future_research": "future research",
}

_COMMON_DEMOGRAPHICS = {
    "first_name": ("legal first name", "first name", "firstname", "given name", "patient first name"),
    "last_name": ("legal last name", "last name", "lastname", "surname", "family name", "patient last name"),
    "pref_name": ("preferred name", "pref name", "preferred", "nickname"),
    "email": ("email", "e-mail", "email address"),
    "dob": ("date of birth", "birthdate", "birth date", "dob", "d.o.b"),
    "phone": ("mobile phone", "phone", "telephone", "mobile", "cell", "contact number"),
    "address": ("address", "home address", "mailing address"),
    "health_card": ("health card", "ohip", "hin", "health number"),
}


TEMPLATES: tuple[QuestionnaireTemplate, ...] = (
    QuestionnaireTemplate(
        type=QuestionnaireType.ADULT_ADHD,
        identifiers=(
            "adult adhd centre",
            "adult adhd assessment",
            "adult adhd questionnaire",
        ),
        demographic_fields=_COMMON_DEMOGRAPHICS,
        checkbox_fields=_COMMON_CHECKBOXES,
        validation_pages=(6, 11),
    ),
    QuestionnaireTemplate(
        type=QuestionnaireType.ADHD_WOMEN,
        identifiers=(
            "adhd centre for women",
            "adhd for women",
            "women's adhd assessment",
            "centre for women",
        ),
        demographic_fields=_COMMON_DEMOGRAPHICS,
        checkbox_fields=_COMMON_CHECKBOXES,
        validation_pages=(6, 12),
    ),
)


def identify_type(text: str) -> QuestionnaireType:
    """Return the questionnaire type whose identifier appears in ``text``."""
    lowered = (text or "").lower()
    for template in TEMPLATES:
        if any(token in lowered for token in template.identifiers):
            return template.type
    return QuestionnaireType.UNKNOWN


def template_for(qtype: QuestionnaireType) -> QuestionnaireTemplate | None:
    for template in TEMPLATES:
        if template.type is qtype:
            return template
    return None
