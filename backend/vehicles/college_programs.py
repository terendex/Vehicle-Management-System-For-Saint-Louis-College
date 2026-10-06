"""The official college programs and their year levels.

The one list a college applicant's "Program & Year" may come from. Kept in code
rather than in tbl_reference_item: the reference rows were never editable from
any screen, had drifted from the school's actual offerings (BSCS, BSN, BSEE...),
and a data change there would have to be made by hand on the shared database.
Code ships to Railway and the campus app the same way.

The registration form bundles an identical copy
(frontend/src/data/collegePrograms.json) so the picker never waits on the
network; test_college_programs fails if the two differ.

Stored as one string, "<code> - <year>" (e.g. "BSIT - 3", "BS Arch - 5",
"MBA - 1"), which is the shape program_year has always had. A School of
Advanced Studies student past coursework is on "Residency" instead of a year
("MBA - Residency").
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
    # vehicle pass can check; the form says so beside the choice. The pass
    # offers Year 1 and Year 2 only.
    ('CLCJE', 'College of Law and Criminal Justice Education', (
        ('BS Crim', 4), ('JD', 2),
    )),
    # The School of Advanced Studies' schedule of fees (S.Y. 2025-2026) is
    # the whole offering: the doctorates PhD and EdD, and the masterals MAEd,
    # MBA, MAGC, MLIS and MPA, each Year 1 and Year 2, plus Residency (below).
    # Codes carry no hyphen: the stored value is "<code> - <year>".
    ('SAS', 'School of Advanced Studies', (
        ('PhD', 2), ('EdD', 2),
        ('MAEd', 2), ('MBA', 2), ('MAGC', 2), ('MLIS', 2), ('MPA', 2),
    )),
)

PROGRAM_YEARS = {code: years for _, _, programs in COLLEGES for code, years in programs}
_BY_LOWER = {code.lower(): code for code in PROGRAM_YEARS}

# A graduate student past coursework, writing the thesis or dissertation, is
# enrolled on Residency rather than a year level: the fee schedule's third
# column. Offered after the year levels of every School of Advanced Studies
# program.
RESIDENCY = 'Residency'
RESIDENCY_PROGRAMS = frozenset(code for college, _, programs in COLLEGES if college == 'SAS'
                               for code, _ in programs)

# Lazy program part, so "BSIT 12" splits as BSIT / 12 rather than "BSIT 1" / 2.
_SHAPE = re.compile(r'^(.*?)\s*-?\s*(\d+|residency)$', re.IGNORECASE)

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
    code, level = _BY_LOWER.get(m.group(1).lower()), m.group(2)
    if code is None:
        return None
    if level.lower() == RESIDENCY.lower():
        return f'{code} - {RESIDENCY}' if code in RESIDENCY_PROGRAMS else None
    if not 1 <= int(level) <= PROGRAM_YEARS[code]:
        return None
    return f'{code} - {int(level)}'


def all_program_years():
    """Every valid value, in list order — what /vehicles/programs/ serves."""
    return [f'{code} - {level}'
            for _, _, programs in COLLEGES
            for code, years in programs
            for level in [*range(1, years + 1), *([RESIDENCY] if code in RESIDENCY_PROGRAMS else [])]]
