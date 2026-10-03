"""The "Browse" picker behind the scheduled-backup folder setting.

It lists the server's folders one level at a time, so it must show folders and
nothing else, refuse anyone but an admin, and turn a bad path into a sentence
rather than a 500.
"""
import os
import shutil
import tempfile

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User

URL = '/api/accounts/system/folders/'


class FolderBrowseTests(TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='slc-browse-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name in ('Beta', 'alpha', '.hidden', '$Recycle.Bin'):
            os.mkdir(os.path.join(self.root, name))
        with open(os.path.join(self.root, 'secret-file.txt'), 'w') as fh:
            fh.write('not for listing')

        self.admin = User.objects.create_user(
            email='browse-admin@slc.edu.ph', last_name='ADMIN', first_name='BROWSE',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def browse(self, path=None, client=None):
        params = {} if path is None else {'path': path}
        return (client or self.client).get(URL, params)

    def test_lists_only_visible_folders_sorted(self):
        res = self.browse(self.root)
        self.assertEqual(res.status_code, 200)
        names = [f['name'] for f in res.json()['folders']]
        self.assertEqual(names, ['alpha', 'Beta'])     # no file, no hidden folders, case-blind order

    def test_each_folder_carries_its_full_path_and_a_way_up(self):
        body = self.browse(self.root).json()
        self.assertEqual(body['folders'][0]['path'], os.path.join(self.root, 'alpha'))
        self.assertEqual(body['parent'], os.path.dirname(os.path.normpath(self.root)))

    def test_no_path_gives_the_starting_points(self):
        body = self.browse().json()
        self.assertEqual(body['path'], '')
        self.assertTrue(body['folders'], 'expected at least one drive or /')
        for item in body['folders']:
            self.assertTrue(os.path.isabs(item['path']))

    def test_the_top_of_a_drive_has_no_parent(self):
        top = os.path.abspath(os.sep)
        self.assertIsNone(self.browse(top).json()['parent'])

    def test_a_missing_folder_is_a_readable_400(self):
        res = self.browse(os.path.join(self.root, 'not-here'))
        self.assertEqual(res.status_code, 400)
        self.assertIn('does not exist', res.json()['error'])

    def test_a_relative_path_is_refused(self):
        res = self.browse('backups-here')
        self.assertEqual(res.status_code, 400)
        self.assertIn('full folder path', res.json()['error'])

    def test_a_file_is_not_a_folder(self):
        res = self.browse(os.path.join(self.root, 'secret-file.txt'))
        self.assertEqual(res.status_code, 400)

    def test_admin_only(self):
        guard = User.objects.create_user(
            email='browse-guard@slc.edu.ph', last_name='GUARD', first_name='BROWSE',
            password='SecurePassword123!', role='security')
        client = APIClient()
        client.force_authenticate(guard)
        self.assertEqual(self.browse(self.root, client=client).status_code, 403)
        self.assertIn(self.browse(self.root, client=APIClient()).status_code, (401, 403))
