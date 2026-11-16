import tempfile
import unittest
from pathlib import Path
from engine.collect.endpoint import Endpoints
from engine.collect.http import CollectionError
from engine.config import read_json, write_json
from engine.nightly import exit_code


def fail(text):
    def action():
        raise CollectionError(text)
    return action


class NightlyExit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root/'raw'/'2026-11-16'
        self.folder.mkdir(parents=True)
        self.cfg = {'data': self.root}

    def run_source(self, source, steps):
        e = Endpoints(self.folder, source)
        for name, action in steps:
            e.save(name, '/'+name, action)
        return e

    def code(self, collect=None):
        return exit_code(self.cfg, '2026-11-16', collect or {'yandex': 'partial', 'google': 'live'})

    def test_optional_endpoint_504_is_recorded_and_exit_zero(self):
        self.run_source('yandex', [('wm_user', lambda: {'user_id': 1}), ('wm_indexing_samples', fail('API HTTP 504'))])
        self.run_source('google', [('gsc_page', lambda: {'rows': []})])
        m = read_json(self.folder/'yandex_manifest.json')
        self.assertTrue(m['stale'])
        self.assertEqual(m['errors'], [{'endpoint': '/wm_indexing_samples', 'error_class': 'CollectionError'}])
        self.assertEqual(m['http_status'], {'/wm_indexing_samples': 504})
        self.assertFalse(m['auth_failed'])
        self.assertEqual(self.code(), 0)

    def test_authorization_error_is_nonzero(self):
        for status in ('401', '403'):
            self.run_source('yandex', [('wm_user', fail('API HTTP '+status)), ('ym_daily', lambda: {})])
            self.run_source('google', [('gsc_page', lambda: {})])
            self.assertEqual(self.code(), 3)

    def test_missing_keychain_is_authorization_error(self):
        e = Endpoints(self.folder, 'yandex')
        e.attempt('yandex/credentials', fail('Keychain unavailable'))
        self.run_source('google', [('gsc_page', lambda: {})])
        self.assertEqual(self.code(), 3)

    def test_all_yandex_endpoints_failed_is_nonzero(self):
        self.run_source('yandex', [('wm_user', fail('API HTTP 504')), ('ym_daily', fail('API HTTP 504'))])
        self.run_source('google', [('gsc_page', lambda: {})])
        self.assertEqual(self.code(), 1)

    def test_google_all_failed_is_nonzero_and_disabled_source_ignored(self):
        self.run_source('google', [('gsc_page', fail('API HTTP 500'))])
        self.assertEqual(self.code(), 1)
        self.assertEqual(self.code({'yandex': 'disabled', 'google': 'disabled'}), 0)

    def test_source_that_raised_is_nonzero(self):
        self.run_source('google', [('gsc_page', lambda: {})])
        self.assertEqual(self.code({'yandex': 'failed', 'google': 'live'}), 1)


if __name__ == '__main__':
    unittest.main()
