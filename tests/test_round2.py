import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine.config import ROOT, read_json, write_json
from engine.collect import access_log, yandex, google
from engine.collect.http import CollectionError
from engine.collect.run import saved
from engine.store.db import connect, upsert, counts
from engine.store.load import load
from engine.signals.run import run
from engine.report.run import report, changes
from engine.cli import main


class AcceptanceFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.date = '2026-11-12'
        self.folder = self.root/'raw'/self.date
        self.folder.mkdir(parents=True)
        self.cfg = {'data': self.root, 'data_dir': str(self.root), 'domain': 'https://example.test',
                    'sections': ['/', '/blog/'], 'article_section': '/blog/',
                    'keychain': {'yandex': {}, 'google': {}}, 'webmaster_host_id': 'example.test',
                    'metrika_counter': '1', 'gsc_property': 'https://example.test/', 'ai_referrers': []}

    def test_nearest_snapshot_both_directions_and_thirty_day_boundary(self):
        with patch.object(access_log, 'ROOT', self.root):
            folder = self.root/'playbooks/ip_ranges'
            for date, prefix in [('2026-11-01', '192.0.2.0/24'), ('2026-11-12', '203.0.113.0/24')]:
                write_json(folder/('ranges-'+date+'.json'), {'date': date, 'families': {'GPTBot': {'payload': {'prefixes': [{'ipv4Prefix': prefix}]}}}})
            self.assertEqual(str(access_log.ranges_for('2026-11-10')['GPTBot'][0]), '203.0.113.0/24')
            self.assertEqual(str(access_log.ranges_for('2026-11-02')['GPTBot'][0]), '192.0.2.0/24')
            self.assertTrue(access_log.ranges_for('2026-10-02'))
            self.assertEqual(access_log.ranges_for('2026-10-01'), {})
            self.assertTrue(access_log.ranges_for('2026-12-12'))
            self.assertEqual(access_log.ranges_for('2026-12-13'), {})

    def test_ip_fetch_isolates_sources_and_keeps_previous_snapshot_on_error(self):
        sources = read_json(ROOT/'playbooks/ip_sources.json')
        with patch.object(access_log, 'ROOT', self.root):
            write_json(self.root/'playbooks/ai_bots.yaml', {'search index': ['Googlebot', 'bingbot', 'CCBot']})
            write_json(self.root/'playbooks/ip_sources.json', {k: sources[k] for k in ('Googlebot', 'bingbot')})
            snapshot = self.root/'playbooks/ip_ranges/ranges-2026-11-12.json'
            write_json(snapshot, {'date': self.date, 'families': {'Googlebot': {'payload': {'prefixes': [{'ipv4Prefix': '192.0.2.0/24'}]}}}})
            def response(url):
                if 'google' in url:
                    raise CollectionError('private')
                return {'prefixes': [{'ipv4Prefix': '203.0.113.0/24'}]}
            with patch('engine.collect.http.request', side_effect=response):
                errors = access_log.fetch_ranges(self.date)
            result = read_json(snapshot)
            self.assertEqual(len(errors), 1)
            self.assertEqual(result['families']['Googlebot']['payload']['prefixes'][0]['ipv4Prefix'], '192.0.2.0/24')
            self.assertIn('bingbot', result['families'])
            self.assertEqual(result['families']['CCBot']['verification'], 'unavailable')
            self.assertNotIn('private', json.dumps(result))

    def test_family_sources_unavailable_and_report_counts(self):
        bots = {f for names in read_json(ROOT/'playbooks/ai_bots.yaml').values() for f in names}
        sources = read_json(ROOT/'playbooks/ip_sources.json')
        self.assertTrue(set(sources) <= bots)
        self.assertTrue({'GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'PerplexityBot', 'Perplexity-User', 'ClaudeBot', 'Claude-User', 'Googlebot', 'bingbot'} <= sources.keys())
        log = self.root/'access.log'
        log.write_text(''.join(json.dumps({'ts': 1794441600, 'status': status, 'request': {'uri': path, 'headers': {'User-Agent': [family], 'X-Forwarded-For': [ip]}}})+'\n'
                               for family, ip, path, status in [('ChatGPT-User', '203.0.113.1', '/', 200), ('ChatGPT-User', '192.0.2.1', '/', 200), ('CCBot', '', '/', 200), ('CCBot', '', '/wp-content/x', 404)]))
        import ipaddress
        with patch.object(access_log, 'ranges_for', return_value={'ChatGPT-User': [ipaddress.ip_network('203.0.113.0/24')]}):
            access_log.parse(dict(self.cfg, access_log=str(log), internal_user_agents=[], client_address_header='X-Forwarded-For'), self.folder/'access_log.json')
        rows = read_json(self.folder/'access_log.json')['requests']
        self.assertEqual([r['verified'] for r in rows], ['verified', 'unverified', 'unavailable', 'spoofed'])
        load(self.cfg, self.date)
        report(self.cfg, self.date)
        payload = read_json(self.root/'reports/2026-11-09.json')
        families = {r['family']: r for r in payload['robot_verification']['families']}
        self.assertEqual(families['CCBot']['unavailable'], 1)
        self.assertEqual(families['CCBot']['spoofed'], 1)
        self.assertEqual(families['ChatGPT-User']['verified'], 1)
        snapshot = read_json(ROOT/'playbooks/ip_ranges/ranges-2026-11-12.json')
        self.assertEqual(snapshot['families']['CCBot']['verification'], 'unavailable')

    def test_yandex_endpoint_failure_continues_and_cli_fails_after_writes(self):
        def response(url, token, body=None):
            if url.endswith('/user'):
                return {'user_id': 1}
            if '/indexing/samples?' in url:
                raise CollectionError('sensitive message must not persist')
            if '/samples?' in url:
                return {'samples': [{'url': 'https://example.test/'}], 'count': 1}
            if '/query-analytics/' in url:
                return {'text_indicator_to_statistics': []}
            if 'api-metrika' in url:
                return {'query': {}, 'data': []}
            return {}
        config = self.root/'site.json'
        write_json(config, {k: str(v) if isinstance(v, Path) else v for k, v in self.cfg.items() if k != 'data'})
        with patch.dict(os.environ, {'SEO_ENGINE_DATA': str(self.root)}), patch.object(yandex, 'keychain', return_value='fake'), patch.object(yandex, 'request', side_effect=response), patch.object(google, 'collect', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['collect', '--site', str(config), '--date', self.date, '--live']), 1)
        manifest = read_json(self.folder/'collection.json')
        self.assertEqual(manifest['errors'], [{'endpoint': '/indexing/samples', 'error_class': 'CollectionError'}])
        self.assertTrue((self.folder/'wm_analytics_url.json').exists())
        self.assertTrue((self.folder/'wm_popular_total_clicks.json').exists())
        self.assertTrue((self.folder/'ym_referrers.json').exists())
        self.assertNotIn('sensitive', json.dumps(manifest))

    def test_metrika_independent_of_webmaster_user_and_other_reports(self):
        def response(url, *args):
            if '/user' in url:
                return {}  # Malformed Webmaster response must not block Metrika.
            if 'ym%3As%3AsearchEngineName' in url:
                raise CollectionError('failed')
            return {'query': {}, 'data': []}
        with patch.object(yandex, 'keychain', return_value='fake'), patch.object(yandex, 'request', side_effect=response):
            errors = yandex.collect(self.cfg, self.folder, '2026-10-13', '2026-11-09')
        self.assertEqual(errors[0]['error_class'], 'KeyError')
        self.assertTrue((self.folder/'ym_daily.json').exists())
        self.assertTrue((self.folder/'ym_referrers.json').exists())

    def test_google_failure_preserves_partial_rows_and_continues(self):
        def response(url, token, body=None, **kwargs):
            if url.endswith('/sitemaps'):
                return {'sitemap': []}
            if body['dimensions'] == ['date']:
                if body['startRow']:
                    raise CollectionError('failed page 2')
                return {'rows': [{'keys': ['2026-11-09'], 'impressions': 1}]*25000}
            return {'rows': []}
        with patch.object(google, 'authorize', return_value='fake'), patch.object(google, 'request', side_effect=response):
            errors = google.collect(self.cfg, self.folder, '2026-10-13', '2026-11-09')
        self.assertEqual(len(errors), 1)
        partial = read_json(self.folder/'gsc_date.json')
        self.assertEqual(len(partial['rows']), 25000)
        self.assertFalse(partial['_complete'])
        self.assertTrue((self.folder/'gsc_sitemaps.json').exists())
        self.assertTrue(read_json(self.folder/'gsc_page.json')['_complete'])

    def test_round_one_schema_migrates_without_losing_freshness_or_rowids(self):
        schema = (ROOT/'engine/store/schema.sql').read_text()
        schema = schema[:schema.index('CREATE TABLE IF NOT EXISTS stat_registry (id')].replace('source_status', 'stat_registry').replace("'unavailable',", '')
        db = sqlite3.connect(self.root/'seo.sqlite')
        db.executescript(schema)
        db.execute("INSERT INTO stat_registry VALUES ('gsc','2026-11-12','2026-11-09','fresh','[]')")
        db.execute("INSERT INTO bot_day (rowid,date,family,class,verified,page,status,hits) VALUES (7,'2026-11-12','GPTBot','training crawler','unverified','/',200,3)")
        db.commit()
        db.close()
        db = connect(self.cfg)
        self.assertEqual(db.execute('SELECT count(*) FROM source_status').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT count(*) FROM stat_registry').fetchone()[0], 0)
        self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(stat_registry)')], ['id','name','value','unit','sample_size','method','source','as_of','page','note'])
        self.assertEqual(db.execute('SELECT rowid FROM bot_day').fetchone()[0], 7)
        db.close()
        db = connect(self.cfg)
        self.assertEqual(db.execute('SELECT count(*) FROM source_status').fetchone()[0], 1)
        db.close()

    def test_referrer_config_keeps_assistants_only(self):
        refs = read_json(ROOT/'sites/lida/site.yaml')['ai_referrers']
        self.assertNotIn('ya.ru', refs)
        self.assertNotIn('openai.com', refs)
        self.assertTrue({'chatgpt.com', 'chat.openai.com', 'alice.yandex.ru'} <= set(refs))
        self.assertEqual(len(refs), 13)

    def test_threshold_boundaries_grouping_and_probe_summary(self):
        self.cfg['signal_thresholds'] = read_json(ROOT/'sites/lida/site.yaml')['signal_thresholds']
        db = connect(self.cfg)
        with db:
            for imp in (19,20,49,50):
                upsert(db, 'page_day', source='gsc', start='2026-10-13', end='2026-11-09', page='/p'+str(imp)+'/', dimension='', grain='window', impressions=imp, clicks=0, position=9, evidence='fixture')
            for query, second in [('below',9), ('boundary',10)]:
                for page,imp in [('/a/',10),('/b/',second)]:
                    upsert(db,'query_day',source='gsc',start='2026-10-13',end='2026-11-09',query=query,page=page,grain='window',impressions=imp,clicks=0,position=9,evidence='fixture')
            fixtures = [('OAI-SearchBot','search index','unverified','/demand/',200,2), ('ChatGPT-User','user-triggered fetch','unverified','/demand/',200,1), ('GPTBot','training crawler','verified','/training/',200,9), ('Codex','coding agents','unavailable','/coding/',200,9), ('ChatGPT-User','user-triggered fetch','spoofed','/spoof/',200,9), ('OAI-SearchBot','search index','unverified','/two/',200,2), ('CCBot','training crawler','unavailable','/missing/',404,2), ('GPTBot','training crawler','unverified','/missing/',404,1), ('GPTBot','training crawler','spoofed','/.env',404,3), ('GPTBot','training crawler','spoofed','/wp-content/file',404,4), ('GPTBot','training crawler','spoofed','/.git/config',404,2)]
            for family,cls,verification,page,status,hits in fixtures:
                upsert(db,'bot_day',date=self.date,family=family,**{'class':cls},verified=verification,page=page,status=status,hits=hits,evidence='fixture')
        db.close()
        result = run(self.cfg,self.date)
        self.assertEqual(result['striking_distance'],3)
        self.assertEqual(result['low_ctr'],1)
        self.assertEqual(result['two_pages_one_query'],1)
        self.assertEqual(result['ai_robot_demand'],1)
        self.assertEqual(result['robot_404'],1)
        self.assertEqual(result['probe_noise'],1)
        db=connect(self.cfg)
        self.assertEqual(db.execute("SELECT subject FROM signal WHERE rule='ai_robot_demand'").fetchone()[0],'/demand/')
        self.assertEqual(db.execute("SELECT score FROM signal WHERE rule='probe_noise'").fetchone()[0],9)
        db.close()

    def test_imports_are_self_contained_and_backfill_date_is_fixed(self):
        cfg = read_json(ROOT/'sites/lida/site.yaml')
        self.assertEqual([r['id'] for r in read_json(ROOT/cfg['effects_file'])], ['E-0001','E-0002','E-0003'])
        self.assertNotIn('.ref/', json.dumps(cfg))
        self.assertNotIn('codex-results', json.dumps(cfg))
        cfg.update(data=self.root, snapshot_dir=str(self.root/'saved'))
        saved(cfg,self.date,False)
        self.assertFalse((self.root/'raw/2026-11-10/wm_index_check.json').exists())
        saved(cfg,self.date,True)
        self.assertTrue((self.root/'raw/2026-11-10/wm_in_search_samples.json').exists())
        self.assertFalse((self.folder/'wm_index_check.json').exists())
        load(cfg,self.date)
        db=connect(cfg)
        self.assertEqual(db.execute('SELECT count(*) FROM experiment').fetchone()[0],3)
        self.assertGreater(db.execute("SELECT count(*) FROM index_day WHERE date='2026-11-10'").fetchone()[0],0)
        db.close()

    def test_window_changes_and_top_ten_per_rule(self):
        db=connect(self.cfg)
        with db:
            for window,imp,clicks in [('2026-11-08',100,10),('2026-11-09',150,12)]:
                upsert(db,'page_day',source='gsc',start='2026-10-13',end=window,page='/blog/a/',dimension='',grain='window',impressions=imp,clicks=clicks)
                if window=='2026-11-08':
                    self.assertFalse(changes(db,self.cfg,self.date)['available'])
            upsert(db,'page_day',source='gsc',start='2026-10-13',end='2026-11-08',page='/lost/',dimension='',grain='window',impressions=20,clicks=5)
            for i in range(12):
                upsert(db,'signal',date=self.date,rule='striking_distance',subject='PAGE'+str(i)+'END',score=i,numbers='{}',source_rows='[]')
        delta=changes(db,self.cfg,self.date)
        self.assertEqual(delta['pages'][0]['page'],'/lost/')
        self.assertEqual(delta['pages'][0]['clicks_change'],-5)
        self.assertEqual(next(r for r in delta['sections'] if r['section']=='/blog/')['impressions_change'],50)
        db.close()
        report(self.cfg,self.date)
        payload=read_json(self.root/'reports/2026-11-09.json')
        self.assertTrue(payload['changes']['available'])
        output=(self.root/'reports/2026-11-09.html').read_text()
        self.assertNotIn('PAGE0END',output)
        self.assertIn('PAGE2END',output)
        self.assertLess(output.index('PAGE11END'),output.index('PAGE10END'))
        self.assertEqual(next(r['count'] for r in payload['signals'] if r['rule']=='striking_distance'),12)
        self.assertEqual(len(payload['changes']['pages']),2)
        db=connect(self.cfg)
        with db:
            db.execute("DELETE FROM page_day WHERE end='2026-11-08'")
        db.close()
        report(self.cfg,self.date)
        self.assertIn('только одно окно', (self.root/'reports/2026-11-09.html').read_text())


if __name__ == '__main__':
    unittest.main()
