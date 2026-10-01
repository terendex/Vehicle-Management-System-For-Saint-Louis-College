"""The official college programs and their year levels.

The one list a college applicant's "Program & Year" may come from. Kept in code
rather than in tbl_reference_item: the reference rows were never editable from
any screen, had drifted from the school's actual offerings (BSCS, BSN, BSEE...),
and a data change there would have to be made by hand on the shared database.
Code ships to Railway and the campus app the same way.

The registration form bundles an identical copy
(frontend/src/data/collegePrograms.json) so the picker never waits on the
network; test_college_programs fails if the two differ.

Stored as one string, "<code> - <year>" (e.g. "BSIT - 3", "BS Arch - 5"),
which is the shape program_year has always had.
"""
from __future__ import annotations

import re

# (college code, college name, ((program code, years), ...))
COLLEGES = (
    ('CASTE', 'College of Arts, Sciences, Technology, and Education', (
        ('BEEd', 4), ('BAELS', 4), ('BAPOS', 4), ('BS Psych', 4), ('BLIS', 4),
        ('BECEd', 4), ('BSNEd', 4), ('BSEd', 4), ('BPEd', 4), ('BTLEd', 4),
        ('BSIT', 4),
    )),
    ('CBA', 'College of Business and Accountancy', (
        ('BSA', 4), ('BSAIS', 4), ('BSIA', 4), ('BSMA', 4), ('BSBA', 4),
        ('BSHM', 4), ('BSTM', 4), ('BSOA', 4),
    )),
    ('CEA', 'College of Engineering and Architecture', (
        ('BS Arch', 5), ('BSCE', 4),
    )),
    # JD (Juris Doctor) admits only students who already hold a bachelor's
    # degree. That is the College of Law's admission rule, not something a
    # vehicle pass can check; the form says so beside the choice.
    ('CLCJE', 'College of Law and Criminal Justice Education', (
        ('BS Crim', 4), ('JD', 4),
    )),
)

PROGRAM_YEARS = {code: years for _, _, programs in COLLEGES for code, years in programs}
_BY_LOWER = {code.lower(): code for code in PROGRAM_YEARS}

# Lazy program part, so "BSIT 12" splits as BSIT / 12 rather than "BSIT 1" / 2.
_SHAPE = re.compile(r'^(.*?)\s*-?\s*(\d+)$')

INVALID_MESSAGE = 'Choose your program and year level from the list.'


def normalize_program_year(text):
    """The canonical 'BSIT - 3', or None if it is not on the list.

    Lenient about how it is written — 'BSIT 3', 'bsit-3', 'BS  Arch - 5' —
    because the old free-text form and direct callers wrote it every way; what
    must match is the program and an offered year.
    """
    m = _SHAPE.match(' '.join((text or '').split()))
    if not m:
        return None
    code, year = _BY_LOWER.get(m.group(1).lower()), int(m.group(2))
    if code is None or not 1 <= year <= PROGRAM_YEARS[code]:
        return None
    return f'{code} - {year}'


def all_program_years():
    """Every valid value, in list order — what /vehicles/programs/ serves."""
    return [f'{code} - {year}'
            for _, _, programs in COLLEGES
            for code, years in programs
            for year in range(1, years + 1)]
