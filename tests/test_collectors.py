import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from engine.collect import google, yandex
from engine.collect.http import CollectionError, keychain, request
from engine.config import read_json


class CollectorsTest(unittest.TestCase):
    def test_google_explicit_window_final_and_daily_joint_dimensions(self):
        cfg = {'gsc_property': 'https://another.test/', 'keychain': {'google': {}}}
        with tempfile.TemporaryDirectory() as tmp:
            def response(url, token, body=None, **kwargs):
                if url.endswith('/sitemaps'):
                    return {'sitemap': []}
                return {'rows': [{'keys': ['fixture'], 'impressions': 1}]}
            with patch.object(google, 'authorize', return_value='not-a-real-token'), patch.object(google, 'request', side_effect=response) as calls:
                google.collect(cfg, Path(tmp), '2026-10-13', '2026-11-09')
            payload = read_json(Path(tmp)/'gsc_date_page_query.json')
            self.assertEqual(payload['_dimensions'], ['date', 'page', 'query'])
            self.assertEqual(payload['_window'], ['2026-10-13', '2026-11-09'])
            for c in calls.call_args_list[:-1]:
                body = c.args[2]
                self.assertEqual(body['dataState'], 'final')
                self.assertEqual(body['startDate'], '2026-10-13')
                self.assertEqual(body['endDate'], '2026-11-09')
            self.assertIn('another.test', calls.call_args.args[0])

    def test_yandex_ranges_pagination_and_referrer_dimension(self):
        cfg = {'keychain': {'yandex': {}}, 'webmaster_host_id': 'https:another.test:443', 'metrika_counter': '123'}
        def response(url, token, body=None):
            if url.endswith('/user'):
                return {'user_id': 1}
            if '/samples?' in url:
                return {'samples': [{'url': 'https://another.test/'}], 'count': 1}
            if '/query-analytics/' in url:
                return {'text_indicator_to_statistics': []}
            if 'api-metrika' in url:
                q = parse_qs(urlsplit(url).query)
                return {'query': {k: v[0] for k, v in q.items()}, 'data': []}
            return {'history': []}
        with tempfile.TemporaryDirectory() as tmp, patch.object(yandex, 'keychain', return_value='not-real'), patch.object(yandex, 'request', side_effect=response) as calls:
            yandex.collect(cfg, Path(tmp), '2026-10-13', '2026-11-09')
            self.assertTrue(read_json(Path(tmp)/'wm_in_search_samples.json')['complete'])
            self.assertEqual(read_json(Path(tmp)/'ym_referrers.json')['query']['dimensions'], 'ym:s:date,ym:s:startURL,ym:s:referer')
            analytics = [c.args[2] for c in calls.call_args_list if '/query-analytics/' in c.args[0]]
            self.assertTrue(all(b['date_from'] <= b['date_to'] for b in analytics))
            histories = [c.args[0] for c in calls.call_args_list if '/history?' in c.args[0]]
            self.assertEqual(len(histories), 3)

    def test_errors_do_not_leak_credentials_or_response_body(self):
        import subprocess
        import urllib.error
        with patch('engine.collect.http.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['security'], stderr='private')):
            with self.assertRaisesRegex(CollectionError, '^Keychain unavailable$'):
                keychain({'account': 'a', 'service': 's'})
        error = urllib.error.HTTPError('https://example.test', 403, 'secret in reason', {}, None)
        with patch('engine.collect.http.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(CollectionError, '^API HTTP 403$'):
                request('https://example.test', token='private')


if __name__ == '__main__':
    unittest.main()
