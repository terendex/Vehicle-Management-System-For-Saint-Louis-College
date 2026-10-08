"""The "Browse" picker behind the scheduled-backup folder setting.

It lists the server's folders one level at a time, so it must show folders and
nothing else, refuse anyone but an admin, and turn a bad path into a sentence
rather than a 500.
"""
import os
import shutil
import subprocess
import tempfile
from unittest import mock, skipUnless

from django.test import SimpleTestCase, TestCase
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


PICK_URL = '/api/accounts/system/folders/pick/'


class FolderPickTests(TestCase):
    """The Windows "Select Folder" window behind Browse. It must only open for
    someone at the server PC; everyone else is told to use the in-app list.
    The window itself is mocked: a test cannot click it."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='pick-admin@slc.edu.ph', last_name='ADMIN', first_name='PICK',
            password='SecurePassword123!', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        desktop = mock.patch('accounts.folder_dialog._has_desktop', return_value=True)
        desktop.start()
        self.addCleanup(desktop.stop)

    def pick(self, path='', client=None, **extra):
        return (client or self.client).post(PICK_URL, {'path': path}, format='json', **extra)

    def test_another_computer_gets_the_in_app_list(self):
        with mock.patch('accounts.folder_dialog.pick_folder') as pick:
            res = self.pick(REMOTE_ADDR='10.250.1.77')
        self.assertEqual(res.json(), {'available': False})
        pick.assert_not_called()

    def test_a_tunnel_or_proxy_counts_as_another_computer(self):
        # ngrok connects from 127.0.0.1, so only the forwarded header gives it away.
        with mock.patch('accounts.folder_dialog.pick_folder') as pick:
            res = self.pick(HTTP_X_FORWARDED_FOR='203.0.113.9')
        self.assertEqual(res.json(), {'available': False})
        pick.assert_not_called()

    def test_no_desktop_gets_the_in_app_list(self):
        with mock.patch('accounts.folder_dialog._has_desktop', return_value=False), \
             mock.patch('accounts.folder_dialog.pick_folder') as pick:
            res = self.pick()
        self.assertEqual(res.json(), {'available': False})
        pick.assert_not_called()

    @skipUnless(os.name == 'nt', 'the folder window is Windows only')
    def test_at_the_server_pc_the_pick_comes_back(self):
        with mock.patch('accounts.folder_dialog.pick_folder', return_value='E:\SLC Backups') as pick:
            res = self.pick('D:\Old')
        self.assertEqual(res.json(), {'available': True, 'path': 'E:\SLC Backups'})
        pick.assert_called_once_with('D:\Old')

    @skipUnless(os.name == 'nt', 'the folder window is Windows only')
    def test_cancel_or_no_answer_changes_nothing(self):
        cancelled = {'return_value': None}
        unanswered = {'side_effect': subprocess.TimeoutExpired('powershell.exe', 300)}
        for outcome in (cancelled, unanswered):
            with mock.patch('accounts.folder_dialog.pick_folder', **outcome):
                res = self.pick()
            self.assertEqual(res.json(), {'available': True, 'path': None})

    @skipUnless(os.name == 'nt', 'the folder window is Windows only')
    def test_a_second_window_is_refused(self):
        from accounts.folder_dialog import FolderDialogBusy
        with mock.patch('accounts.folder_dialog.pick_folder', side_effect=FolderDialogBusy()):
            res = self.pick()
        self.assertEqual(res.status_code, 409)
        self.assertIn('already open', res.json()['error'])

    @skipUnless(os.name == 'nt', 'the folder window is Windows only')
    def test_a_window_that_will_not_open_falls_back(self):
        with mock.patch('accounts.folder_dialog.pick_folder', side_effect=RuntimeError('Add-Type failed')):
            res = self.pick()
        self.assertEqual(res.json(), {'available': False})

    def test_admin_only(self):
        guard = User.objects.create_user(
            email='pick-guard@slc.edu.ph', last_name='GUARD', first_name='PICK',
            password='SecurePassword123!', role='security')
        client = APIClient()
        client.force_authenticate(guard)
        with mock.patch('accounts.folder_dialog.pick_folder') as pick:
            self.assertEqual(self.pick(client=client).status_code, 403)
            self.assertIn(self.pick(client=APIClient()).status_code, (401, 403))
        pick.assert_not_called()


class FolderDialogErrorTextTests(SimpleTestCase):
    """With its streams redirected, PowerShell reports errors as CLIXML. The
    server log should say what failed, not "#< CLIXML"."""

    def test_clixml_error_becomes_one_readable_line(self):
        from accounts.folder_dialog import _error_text
        raw = ('#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">Add-Type : Cannot add type. '
               '&amp; it failed_x000D__x000A_</S><S S="Error">At line:3 char:1_x000D__x000A_</S></Objs>').encode()
        self.assertEqual(_error_text(raw), 'Add-Type : Cannot add type. & it failed At line:3 char:1')

    def test_plain_text_passes_through(self):
        from accounts.folder_dialog import _error_text
        self.assertEqual(_error_text(b'powershell.exe: access denied\r\n'), 'powershell.exe: access denied')
