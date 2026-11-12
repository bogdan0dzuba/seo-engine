import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine import config
from engine.cli import main
from engine.collect.run import collect
from engine.collect import yandex
from engine.proposals.run import propose, card
from engine.signals.run import freshness
from engine.store.db import connect, upsert


class PortableProposals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root/'data'
        self.data.mkdir()
        self.cfg = {'domain': 'https://example-shop.test', 'sections': ['/', '/guides/'],
                    'data': self.data, 'data_dir': str(self.data), 'article_section': '/guides/'}
        self.date = '2026-11-12'

    def command(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(argv))

    def seed(self):
        db = connect(self.cfg)
        with db:
            for path, published in [('/guides/a/', '2026-08-01'), ('/guides/b/', '2026-08-02'),
                                    ('/guides/c/', '2026-08-03'), ('/guides/young/', '2026-10-16'),
                                    ('/guides/unknown/', None)]:
                upsert(db, 'page', path=path, section='/guides/', cluster='widgets', published=published, current_crawl=self.date)
                upsert(db, 'page_day', source='gsc', start='2026-10-13', end='2026-11-09', page=path,
                       grain='window', impressions=100 if path.endswith('a/') else 110, clicks=10, position=9)
        return db

    def signal(self, db, rule, subject, numbers=None, date=None):
        with db:
            upsert(db, 'signal', date=date or self.date, rule=rule, subject=subject, score=999,
                   numbers=json.dumps(numbers or {'impressions': 100, 'window': ['2026-10-13', '2026-11-09']}), source_rows='[]')

    def proposals(self):
        db = connect(self.cfg)
        result = [json.loads(r[0]) for r in db.execute('SELECT payload FROM proposal ORDER BY id')]
        db.close()
        return result

    def test_init_to_card_offline_fixtures(self):
        # Exercise the real CLI in an isolated root, retaining production playbooks.
        (self.root/'sites/_template').mkdir(parents=True)
        (self.root/'sites/_template/site.yaml').write_text((config.ROOT/'sites/_template/site.yaml').read_text())
        with patch.object(config, 'ROOT', self.root), patch.dict(os.environ, {'SEO_ENGINE_DATA': str(self.data)}):
            self.assertEqual(self.command('init-site', '--site', 'example-shop', '--domain', self.cfg['domain'], '--data-dir', str(self.data)), 0)
            path = self.root/'sites/example-shop/site.yaml'
            cfg = config.read_json(path)
            self.assertEqual(set(cfg), {'domain', 'sections', 'data_dir'})
            with self.assertRaises(FileExistsError):
                config.init_site('example-shop', self.cfg['domain'])
            files = self.root/'html'
            files.mkdir()
            urls = ['/', '/guides/a/', '/guides/b/']
            for url in urls:
                f = files/url.strip('/')/'index.html'
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text('<title>'+url+'</title><meta name="description" content="'+url+'"><link rel="canonical" href="'+self.cfg['domain']+url+'"><meta property="article:published_time" content="2026-08-01"><h1>Guide</h1>'+('word '*310)+'<a href="/guides/a/">a</a><a href="/guides/b/">b</a>')
            (files/'sitemap.xml').write_text('<urlset>'+''.join('<url><loc>'+self.cfg['domain']+p+'</loc></url>' for p in urls)+'</urlset>')
            cfg.update(site_files=str(files), sitemap='sitemap.xml', sections=['/', '/guides/'], article_section='/guides/', snapshot_dir=str(self.root/'fixtures'))
            config.write_json(path, cfg)
            config.write_json(self.root/'fixtures'/self.date/'gsc_page.json', {'_window': ['2026-10-13', '2026-11-09'], 'rows': [
                {'keys': [self.cfg['domain']+'/guides/a/'], 'impressions': 100, 'clicks': 1, 'position': 5},
                {'keys': [self.cfg['domain']+'/guides/b/'], 'impressions': 110, 'clicks': 12, 'position': 6}]})
            with patch('engine.collect.http.keychain', side_effect=AssertionError('No secrets')), patch('engine.collect.http.request', side_effect=AssertionError('No network')):
                for command in ('collect', 'crawl', 'load', 'signals', 'propose', 'card'):
                    self.assertEqual(self.command(command, '--site', 'example-shop', '--date', self.date), 0, command)
            result = config.read_json(self.data/'cards/2026-11-09.json')
            self.assertEqual(result['proposal']['page'], '/guides/a/')
            self.assertEqual(result['proposal']['control'], '/guides/b/')
            self.assertIn('Как откатить:', result['text'])
            self.assertEqual(len(self.proposals()), 2)  # low CTR + observed topic demand

    def test_absent_sources_are_disabled_without_credentials(self):
        with patch('engine.collect.run.google.collect', side_effect=AssertionError('disabled')), patch('engine.collect.run.yandex.collect', side_effect=AssertionError('disabled')):
            result = collect(self.cfg, self.date, live=True)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['google'], 'disabled')
        db = connect(self.cfg)
        self.assertEqual(freshness(db, self.date, self.cfg), [])
        db.close()
        cfg_file = self.root/'minimal.json'
        config.write_json(cfg_file, {k: v for k, v in self.cfg.items() if k != 'data'})
        for command in ('collect', 'crawl', 'load', 'signals', 'propose', 'card', 'report'):
            self.assertEqual(self.command(command, '--site', str(cfg_file), '--date', self.date), 0)

    def test_metrika_only_and_webmaster_only_are_independent(self):
        for field in ('metrika_counter', 'webmaster_host_id'):
            cfg = dict(self.cfg, keychain={'yandex': {'account': 'test', 'service': 'test'}}, **{field: '1'})
            def response(url, *args):
                if url.endswith('/user'):
                    return {'user_id': 1}
                return {}
            with patch.object(yandex, 'keychain', return_value='fixture'), patch.object(yandex, 'request', side_effect=response) as calls:
                yandex.collect(cfg, self.data, '2026-10-13', '2026-11-09')
            urls = [c.args[0] for c in calls.call_args_list]
            self.assertEqual(any('api-metrika' in u for u in urls), field == 'metrika_counter')
            self.assertEqual(any('webmaster.yandex' in u for u in urls), field == 'webmaster_host_id')

    def test_age_active_and_control_exclusions_score_and_idempotence(self):
        db = self.seed()
        for p in ('a', 'young', 'unknown'):
            self.signal(db, 'low_ctr', '/guides/'+p+'/')
        with db:
            upsert(db, 'experiment', id='active', date='2026-11-01', status='running', record=json.dumps({'pages': ['/guides/b/']}), source='fixture')
        db.close()
        self.assertEqual(propose(self.cfg, self.date)['proposals'], 1)
        p = self.proposals()[0]
        self.assertEqual(p['control'], '/guides/c/')
        self.assertEqual(p['score'], p['reach']*p['potential']*p['confidence'])
        self.assertIn('Гипотеза:', p['experiment_record'])
        propose(self.cfg, self.date)
        self.assertEqual(len(self.proposals()), 1)
        db = connect(self.cfg)
        with db:
            upsert(db, 'experiment', id='legacy', date='2026-11-11', status='unassessed', record='Страница `https://example-shop.test/guides/a/`', source='fixture')
        db.close()
        self.assertEqual(propose(self.cfg, self.date)['proposals'], 0)

    def test_all_edit_types_high_separate_weekly_choice_and_withdrawal(self):
        db = self.seed()
        self.signal(db, 'tech_issues', '/guides/a/', {'issues': ['missing_title', 'orphan', 'thin_lt300', 'nonself_canonical', 'invalid_jsonld']})
        self.signal(db, 'cluster_traction', 'widgets')
        self.signal(db, 'two_pages_one_query', 'widgets', {'pages': ['/guides/a/', '/guides/b/'], 'impressions': 500})
        db.close()
        propose(self.cfg, self.date)
        rows = self.proposals()
        self.assertTrue({'title/description', 'внутренние ссылки', 'переписывание', 'техническая правка', 'новая статья', 'объединение'} <= {p['kind'] for p in rows})
        first = card(self.cfg, self.date)
        self.assertIn(first['proposal']['risk'], ('low', 'medium'))
        self.assertEqual(len(first['needs_consent']), 2)
        self.assertIn('Нужно согласие', first['text'])
        db = connect(self.cfg)
        self.signal(db, 'low_ctr', '/guides/c/', {'impressions': 100000}, date='2026-11-13')
        db.close()
        propose(self.cfg, '2026-11-13')
        self.assertEqual(card(self.cfg, '2026-11-13')['proposal']['id'], first['proposal']['id'])
        self.assertNotEqual(card(self.cfg, '2026-11-16')['week'], first['week'])
        db = connect(self.cfg)
        with db:
            upsert(db, 'experiment', id='new', date='2026-11-13', status='running', record=json.dumps({'page': first['proposal']['page']}), source='fixture')
        db.close()
        self.assertTrue(card(self.cfg, '2026-11-13')['withdrawn'])

    def test_age_boundary_all_merge_pages_and_high_price_or_removal(self):
        db = self.seed()
        with db:
            db.execute("UPDATE page SET published='2026-10-12' WHERE path='/guides/a/'")
        self.signal(db, 'low_ctr', '/guides/a/')  # exactly 28 at window end
        self.signal(db, 'two_pages_one_query', 'query', {'pages': ['/guides/a/', '/guides/young/']})
        self.signal(db, 'price_change', '/guides/a/')
        self.signal(db, 'remove_page', '/guides/a/')
        db.close()
        propose(self.cfg, self.date)
        rows = self.proposals()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(p['risk'] == 'high' for p in rows), 2)
        self.assertNotIn('объединение', [p['kind'] for p in rows])

    def test_init_rejects_invalid_names_and_domain_before_writing(self):
        with patch.object(config, 'ROOT', self.root):
            for name, domain in [('../escape', 'https://example.org'), ('good', 'file:///tmp'),
                                 ('good', 'https://example.org/path'), ('good', 'https://user:pass@example.org')]:
                with self.assertRaises(ValueError):
                    config.init_site(name, domain)
        self.assertFalse((self.root/'sites').exists())

    def test_only_high_has_no_weekly_action(self):
        db = self.seed()
        self.signal(db, 'tech_issues', '/guides/a/', {'issues': ['nonself_canonical', 'noindex']})
        db.close()
        propose(self.cfg, self.date)
        result = card(self.cfg, self.date)
        self.assertIsNone(result['proposal'])
        self.assertEqual(result['needs_consent'][0]['risk'], 'high')

    def test_core_and_ops_have_no_project_specific_names(self):
        for folder in ('engine', 'ops'):
            for p in (config.ROOT/folder).rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                    text = p.read_text().lower()
                    for name in ('lida', 'лида', 'lida-ai', '~/lida'):
                        self.assertNotIn(name, text, str(p))
