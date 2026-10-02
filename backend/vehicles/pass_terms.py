"""The Vehicle Pass Terms and Conditions, as the registration emails carry them.

The applicant agrees to these on the registration form, and the emails repeat
them so the person has a copy of what they signed up to outside the site.

The wording is the form's (frontend/src/pages/Register/RegisterPage.jsx, under
"Terms & Conditions"; the Policies page repeats it). Change all three together.
Kept as plain text with **bold** markers so the HTML and plain-text parts of a
mail are built from the one list.
"""

INTRO = 'I agree and promise to abide by the terms and conditions anent my application for a vehicle pass.'


def general_terms(fee, window_days):
    """The numbered terms. The fee line depends on what this applicant owes."""
    if fee:
        fee_line = (f'To pay the Vehicle Pass fee of **PHP {fee:,.2f}** at the **Accounting Office**, '
                    f'and to upload the Official Receipt (OR) using the link sent to my email '
                    f'**within {window_days} days of submitting**, failing which this application '
                    f'expires and must be submitted again.')
    else:
        fee_line = ('To settle the Vehicle Pass fee assessed for my department at the '
                    '**Accounting Office**, where one applies.')
    return [
        'I understand that the vehicle pass is intended **ONLY TO ALLOW THE ENTRY OF MY VEHICLE '
        'IN THE CAMPUS**. The College does not guarantee the availability of parking spaces;',
        'The application for a vehicle pass is subject to the approval or disapproval of the '
        'Student Affairs Office;',
        fee_line,
        'As a responsible individual, I promise to:',
    ]


PROMISES = [
    'deactivate vehicle alarm while it is parked within the school premises;',
    'see to it that my vehicle pass is placed on the dashboard, driver side, upon entry and '
    'during the entire stay inside the campus;',
    '**recognize the right of the school to decline the entry of my vehicle if the parking '
    'area is full;**',
    'be courteous to the school security and personnel and fellow parking space users;',
    'allow the school security team to inspect my vehicle, as the need arises, before entry '
    'and when inside the campus;',
    'strictly observe the speed limit of 10 kph within the campus;',
    'park my vehicle at the designated parking area only so as not to obstruct the flow of '
    'traffic inside the campus. **"NO DOUBLE PARKING"**;',
    'not stay inside my vehicle while the engine is on and parked for safety and environmental '
    'reasons;',
    'observe the **"No blowing of horn inside the campus"** policy;',
    'avoid playing loud music or making unnecessary sounds using my vehicle upon entry;',
    'strictly observe the **"No Smoking"** policy of the Institution. **Using e-cigarettes '
    'and/or vapes is not allowed**;',
    'properly lock and secure my vehicle while inside the campus as the College Administration '
    'is **NOT LIABLE** for anything that may happen to the vehicle while it is parked inside '
    'the campus;',
    'strictly observe traffic and/or coding scheme imposed;',
    'follow the above terms and conditions and any violation committed thereto would subject '
    'me to the following sanctions:',
]

SANCTIONS = [
    ('First Offense',  'Restriction of vehicle pass for one (1) week.'),
    ('Second Offense', 'Restriction of vehicle pass for two (2) weeks.'),
    ('Third Offense',  'Restriction of vehicle pass. Prohibition in securing a vehicle pass '
                       'for the next school year.'),
]
