"""ADHD Intake Automation.

A Windows desktop application that automates the intake of ADHD assessment
questionnaires (Adult ADHD Centre and ADHD Centre for Women) from PDF files:

    * classify each PDF as fillable or scanned
    * extract demographics and questionnaire answers (OCR when needed)
    * validate that the consent page is signed
    * look the patient up in OSCAR Pro and upload the document
    * log the result to Google Sheets and a local SQLite database

The package is intentionally modular; each concern lives in its own
sub-package (see the module list in the project README).
"""

__version__ = "1.0.0"
__app_name__ = "ADHD Intake Automation"
