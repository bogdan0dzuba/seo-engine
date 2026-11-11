import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine.config import load as config, write_json, read_json
from engine.collect.run import collect
from engine.collect.access_log import parse, assistant
from engine.collect.freshness import assert_fresh, real_dates, yandex_evidence
from engine.crawl.run import crawl, Page
from engine.store.db import connect, counts, upsert
from engine.store.load import load, load_gsc, load_wm
from engine.signals.run import run, cohorts, cohort, freshness
from engine.report.run import report
from engine.cli import main


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.site = self.root/'site'
        self.site.mkdir()
        self.data = self.root/'data'
        self.data.mkdir()
        self.cfg = {'domain': 'https://second.test', 'data': self.data, 'data_dir': str(self.data),
                    'site_files': str(self.site), 'sitemap': 'sitemap.xml', 'sections': ['/', '/articles/'],
                    'article_section': '/articles/', 'internal_user_agents': ['python-urllib', 'Headless', 'DeployCheck'],
                    'client_address_header': 'X-Forwarded-For', 'ai_referrers': ['chatgpt.com'],
                    'snapshot_dir': str(self.root/'saved'), 'signal_thresholds': {'min_impressions': 1}}
        self.date = '2026-11-12'
        self.folder = self.data/'raw'/self.date
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def html(self, path, date):
        f = self.site/path.strip('/')/'index.html'
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('<html><head><title>Example '+path+'</title><meta name="description" content="Test"><link rel="canonical" href="https://second.test'+path+'"><script type="application/ld+json">'+json.dumps({'@type': 'Article', 'datePublished': date})+'</script></head><body><h1>Title</h1><p>words text</p><a href="/articles/old/">old</a></body></html>')

    def seed(self):
        dates = {'/': '2026-01-01', '/articles/old/': '2026-09-01', '/articles/young/': '2026-11-08', '/articles/future/': '2026-11-11'}
        for p, day in dates.items():
            self.html(p, day)
        (self.site/'sitemap.xml').write_text('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>https://second.test'+p+'</loc></url>' for p in dates)+'</urlset>')
        write_json(self.folder/'gsc_page.json', {'_window': ['2026-10-13', '2026-11-09'], 'rows': [{'keys': ['https://second.test/'], 'impressions': 100, 'clicks': 1, 'position': 9}]})
        write_json(self.folder/'gsc_date.json', {'rows': [{'keys': ['2026-11-09'], 'impressions': 100, 'clicks': 1}]})
        write_json(self.folder/'gsc_page_query.json', {'rows': [{'keys': ['https://second.test/', 'test'], 'impressions': 60, 'clicks': 1, 'position': 9}, {'keys': ['https://second.test/articles/old/', 'test'], 'impressions': 10, 'clicks': 0, 'position': 12}]})

    def test_freshness_stale_and_all_zero(self):
        for dates in ([['2026-10-07', 1]], [['2026-11-12', 0]], {'date_to': '2026-11-12', 'history': [{'date': '2026-11-12', 'value': 0.0}]}):
            with self.assertRaisesRegex(ValueError, 'STALE'):
                assert_fresh(max(real_dates(dates), default=None), self.date)
        self.assertEqual(assert_fresh('2026-11-05', self.date)['age_days'], 7)
        with self.assertRaises(ValueError):
            assert_fresh('2026-11-04', self.date)
        with self.assertRaises(ValueError):
            assert_fresh('2026-11-13', self.date)

    def test_webmaster_ignores_zero_filler_and_request_dates(self):
        p = {'wm_queries_history': {'history': [{'date': '2026-11-12', 'value': 0}]},
             'wm_index_check': {'in_search_history': [['2026-10-07', 1]], 'indexing_history': {'HTTP_2XX': [['2026-10-06', 1]]}, 'sitemaps': [{'last_access_date': '2026-10-09T23:00:00Z'}]}}
        ev = yandex_evidence(p)
        self.assertEqual(ev, {'in_search_history': '2026-10-07', 'indexing_history': '2026-10-06', 'sitemap_last_access': '2026-10-09'})
        with self.assertRaises(ValueError):
            assert_fresh(max(ev.values()), self.date)

    def test_age_at_window_end(self):
        end = '2026-11-09'
        self.assertEqual(cohort('2026-10-12', end), '28+')
        self.assertEqual(cohort('2026-10-13', end), '14–27')
        self.assertEqual(cohort('2026-10-27', end), '7–13')
        self.assertEqual(cohort('2026-11-03', end), '0–6')
        self.assertEqual(cohort('2026-11-10', end), 'after window')
        self.assertEqual(cohort(None, end), 'unknown')

    def test_other_site_whole_cli_pipeline_and_age_rule(self):
        self.seed()
        cfg_file = self.root/'second.yaml'
        write_json(cfg_file, {k: str(v) if isinstance(v, Path) else v for k, v in self.cfg.items() if k != 'data'})
        with patch.dict(os.environ, {'SEO_ENGINE_DATA': str(self.data)}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for command in ('collect', 'crawl', 'load', 'signals', 'report'):
                code = main([command, '--site', str(cfg_file), '--date', self.date])
                self.assertEqual(code, 2 if command == 'signals' else 0)
        db = connect(self.cfg)
        sig = [r[0] for r in db.execute("SELECT subject FROM signal WHERE rule='no_impressions'")]
        self.assertEqual(sig, ['/articles/old/'])
        groups, window = cohorts(db, self.cfg, self.date)
        self.assertEqual(window, ['2026-10-13', '2026-11-09'])
        self.assertEqual(sum(g['pages'] for g in groups), 3)
        self.assertTrue(all(g['share_eligible'] is None for g in groups if g['cohort'] != '28+'))
        self.assertEqual(db.execute("SELECT count(*) FROM signal WHERE rule='two_pages_one_query'").fetchone()[0], 1)
        db.close()
        output = (self.data/'reports/2026-11-09.html').read_text()
        self.assertIn('https://second.test', output)
        self.assertNotIn('<script', output)

    def test_loaders_idempotent_and_stable_rowids(self):
        self.seed()
        crawl(self.cfg, self.date)
        first = load(self.cfg, self.date)
        db = connect(self.cfg)
        ids = [tuple(r) for r in db.execute('SELECT rowid,page FROM page_day ORDER BY rowid')]
        db.close()
        second = load(self.cfg, self.date)
        self.assertEqual(first, second)
        db = connect(self.cfg)
        self.assertEqual(ids, [tuple(r) for r in db.execute('SELECT rowid,page FROM page_day ORDER BY rowid')])
        db.close()

    def log_line(self, ua, ip='203.0.113.7', uri='/x/', status=200, ts=1794355200):
        return json.dumps({'ts': ts, 'status': status, 'request': {'uri': uri, 'remote_ip': '127.0.0.1', 'headers': {'User-Agent': [ua], 'X-Forwarded-For': [ip], 'Referer': ['https://chatgpt.com/a']}}})+'\n'

    def test_internal_ua_excluded_and_forwarded_ip_verification(self):
        f = self.root/'access.log'
        f.write_text(''.join(self.log_line(ua) for ua in ('python-urllib/3.14', 'HeadlessChrome', 'DeployCheck', 'ChatGPT-User', 'ChatGPT-User'))+self.log_line('GPTBot', uri='/.env', status=404))
        self.cfg['access_log'] = str(f)
        import ipaddress
        with patch('engine.collect.access_log.ranges_for', return_value={'ChatGPT-User': [ipaddress.ip_network('203.0.113.0/24')]}):
            summary = parse(self.cfg, self.folder/'access_log.json')
        self.assertEqual(summary['excluded_internal'], 3)
        rows = read_json(self.folder/'access_log.json')['requests']
        self.assertEqual([r['verified'] for r in rows], ['verified', 'verified', 'spoofed'])
        self.assertEqual(len({r['id'] for r in rows}), 3)
        a = load(self.cfg, self.date)
        parse(self.cfg, self.folder/'access_log.json')
        b = load(self.cfg, self.date)
        self.assertEqual(a, b)
        # A later truncated log must not remove durable old daily rows.
        f.write_text(self.log_line('PerplexityBot', ts=1794441600))
        parse(self.cfg, self.folder/'access_log.json')
        c = load(self.cfg, self.date)
        self.assertEqual(c['log_request'], 4)
        db = connect(self.cfg)
        self.assertEqual(db.execute('SELECT sum(hits) FROM bot_day').fetchone()[0], 4)
        db.close()

    def test_untrusted_missing_ip_and_domain_boundary(self):
        self.assertEqual(assistant('https://chatgpt.com.evil.test', ['chatgpt.com']), '')
        self.assertEqual(assistant('https://chatgpt.com/x', ['chatgpt.com']), 'chatgpt.com')
        f = self.root/'access.log'
        f.write_text(self.log_line('Claude-User', ip=''))
        self.cfg['access_log'] = str(f)
        parse(self.cfg, self.folder/'access_log.json')
        self.assertEqual(read_json(self.folder/'access_log.json')['requests'][0]['verified'], 'unverified')

    def test_overlapping_snapshots_are_not_added(self):
        self.seed()
        previous = self.data/'raw/2026-11-11'
        previous.mkdir()
        write_json(previous/'gsc_page.json', {'rows': [{'keys': ['https://second.test/'], 'impressions': 90, 'clicks': 1, 'position': 10}]})
        load(self.cfg, self.date)
        run(self.cfg, self.date)
        db = connect(self.cfg)
        nums = json.loads(db.execute("SELECT numbers FROM signal WHERE rule='striking_distance'").fetchone()[0])
        self.assertEqual(nums['impressions'], 100)
        db.close()

    def test_webmaster_complementary_not_joint_measurement(self):
        payload = [{'status': 200, 'payload': {'text_indicator_to_statistics': [{'text_indicator': {'value': 'keyword'}, 'popular_complementary_indicator': {'value': '/'}, 'statistics': [{'date': '2026-10-01', 'field': 'IMPRESSIONS', 'value': 10}]}]}}]
        write_json(self.folder/'wm_analytics_query.json', payload)
        load(self.cfg, self.date)
        db = connect(self.cfg)
        self.assertEqual(db.execute('SELECT page FROM query_day').fetchone()[0], '')
        db.close()

    def test_panel_citation_and_verified_fetch_rank_trust(self):
        self.seed()
        write_json(self.folder/'ai_panel.json', [{'date': self.date, 'model': 'fixture', 'question_id': 'q1', 'mentioned': True, 'cited_urls': ['https://second.test/articles/old/', 'https://evil.test/'], 'competitor_names': ['Other'], 'offer_phrase_found': False, 'raw_answer_path': 'fixture.txt'}])
        load(self.cfg, self.date)
        db = connect(self.cfg)
        with db:
            upsert(db, 'bot_day', date=self.date, family='ChatGPT-User', **{'class': 'user-triggered fetch'}, verified='verified', page='/articles/old/', status=200, hits=2, special=0, evidence='fixture')
        db.close()
        run(self.cfg, self.date)
        db = connect(self.cfg)
        row = db.execute("SELECT * FROM signal WHERE rule='agent_trust_page'").fetchone()
        self.assertEqual(row['score'], 3)
        self.assertEqual(json.loads(row['numbers'])['panel_citations'], 1)
        db.close()

    def test_no_index_signal_from_incomplete_sample(self):
        self.seed()
        crawl(self.cfg, self.date)
        write_json(self.folder/'wm_in_search_samples.json', {'samples': [{'url': 'https://second.test/'}], 'count': 100, 'complete': False})
        load(self.cfg, self.date)
        counts_ = run(self.cfg, self.date)
        self.assertEqual(counts_['not_in_yandex_index'], 0)

    def test_html_metadata_visible_text_and_schema(self):
        self.seed()
        result = crawl(self.cfg, self.date)
        self.assertEqual(result['pages'], 4)
        row = read_json(self.folder/'crawl.json')[0]
        self.assertEqual(row['jsonld_types'], ['Article'])
        self.assertTrue(row['sha256'])
        p = Page('<head><title>Not visible</title></head><p>Visible</p><script>hidden</script><div hidden>secret</div>')
        self.assertEqual(''.join(p.visible), 'Visible')

    def test_no_site_name_in_core(self):
        core = Path(__file__).resolve().parents[1]/'engine'
        self.assertFalse(any('lida' in p.read_text().lower() for p in core.rglob('*.py')))


if __name__ == '__main__':
    unittest.main()
