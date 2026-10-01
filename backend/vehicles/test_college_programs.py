"""The official college program list, and that it is the only one accepted.

A college applicant's "Program & Year" used to be free text, so the same
program was on file every way it could be typed. It is now picked from
vehicles/college_programs.py, on the form, on submit, and on an edit — and the
form's bundled copy (frontend/src/data/collegePrograms.json) must match it.
"""
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from vehicles.college_programs import (COLLEGES, PROGRAM_YEARS, all_program_years,
                                       normalize_program_year)
from vehicles.models import VehicleRegistration
from vehicles.test_registration_payment import PaymentTestCase

FRONTEND_COPY = Path(settings.BASE_DIR).parent / 'frontend' / 'src' / 'data' / 'collegePrograms.json'


class CatalogTests(SimpleTestCase):

    def test_frontend_copy_matches(self):
        """The form's dropdown and the server's check must offer the same list."""
        bundled = json.loads(FRONTEND_COPY.read_text(encoding='utf-8'))
        self.assertEqual(
            [(c['college'], c['name'], tuple((p['code'], p['years']) for p in c['programs']))
             for c in bundled],
            [(code, name, tuple(programs)) for code, name, programs in COLLEGES],
            'frontend/src/data/collegePrograms.json and vehicles/college_programs.py differ')

    def test_the_list_as_given(self):
        self.assertEqual(len(PROGRAM_YEARS), 23)
        self.assertEqual(PROGRAM_YEARS['BS Arch'], 5)
        self.assertTrue(all(y == 4 for code, y in PROGRAM_YEARS.items() if code != 'BS Arch'))
        for gone in ('BSCS', 'BSN', 'BSEE', 'BSME', 'AB Communication'):
            self.assertNotIn(gone, PROGRAM_YEARS)

    def test_normalising(self):
        self.assertEqual(normalize_program_year('BSIT - 3'), 'BSIT - 3')
        self.assertEqual(normalize_program_year('BSIT 3'), 'BSIT - 3')
        self.assertEqual(normalize_program_year('  bsit-3 '), 'BSIT - 3')
        self.assertEqual(normalize_program_year('bs  psych 2'), 'BS Psych - 2')
        self.assertEqual(normalize_program_year('BS Arch - 5'), 'BS Arch - 5')

    def test_refusing(self):
        for bad in ('', 'BSIT', 'BSIT - 0', 'BSIT - 5', 'BSIT 12', 'BS Arch - 6',
                    'BSCS - 2', 'Info Tech - 1', 'SHS - STEM - Grade 11'):
            self.assertIsNone(normalize_program_year(bad), bad)

    def test_program_list_endpoint_serves_the_official_list(self):
        names = all_program_years()
        self.assertIn('BS Arch - 5', names)
        self.assertNotIn('BSIT - 5', names)
        self.assertEqual(len(names), 22 * 4 + 5)


class SubmitTests(PaymentTestCase):

    def test_college_program_is_stored_canonically(self):
        reg = self.submit(program_year='bsa 2')
        self.assertEqual(reg.program_year, 'BSA - 2')

    def test_program_off_the_list_is_refused(self):
        res = self.client.post('/api/vehicles/register/open/', dict(
            registrant_type='student', last_name='OFF', first_name='LIST', email='off@slc.edu.ph',
            plate_number='OFF 0001', vehicle_type='car', drivers_license='N01-20-880001',
            program_year='BSCS - 2', campus_days=['Monday'], student_level='college',
        ), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('program', res.data['error'].lower())
        self.assertFalse(VehicleRegistration.objects.filter(email='off@slc.edu.ph').exists())

    def test_year_beyond_the_program_is_refused(self):
        res = self.client.post('/api/vehicles/register/open/', dict(
            registrant_type='student', last_name='TOO', first_name='LONG', email='five@slc.edu.ph',
            plate_number='FIV 0005', vehicle_type='car', drivers_license='N01-20-880005',
            program_year='BSIT - 5', campus_days=['Monday'], student_level='college',
        ), format='json')
        self.assertEqual(res.status_code, 400)

    def test_programs_endpoint(self):
        res = self.client.get('/api/vehicles/programs/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, all_program_years())


class EditTests(PaymentTestCase):

    def edit(self, reg, **changes):
        return self.client.post('/api/vehicles/register/details/',
                                {'token': str(reg.payment_token), **changes}, format='json')

    def test_edit_must_come_from_the_list(self):
        reg = self.submit()
        res = self.edit(reg, program_year='Info Tech - 2')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('program_year', res.data['errors'])

    def test_edit_from_the_list_is_saved(self):
        reg = self.submit()
        res = self.edit(reg, program_year='BS Arch - 5')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual(reg.program_year, 'BS Arch - 5')

    def test_older_value_off_the_list_does_not_block_other_edits(self):
        reg = self.submit()
        VehicleRegistration.objects.filter(pk=reg.pk).update(program_year='BSCS - 2')
        res = self.edit(reg, program_year='BSCS - 2', vehicle_color='RED')
        self.assertEqual(res.status_code, 200, res.data)
        reg.refresh_from_db()
        self.assertEqual((reg.program_year, reg.vehicle_color), ('BSCS - 2', 'RED'))

    def test_other_levels_keep_their_free_text(self):
        reg = self.submit()
        VehicleRegistration.objects.filter(pk=reg.pk).update(student_level='shs',
                                                             program_year='SHS - STEM - Grade 11')
        res = self.edit(reg, program_year='SHS - ABM - Grade 12')
        self.assertEqual(res.status_code, 200, res.data)
