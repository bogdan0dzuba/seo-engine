"""Deterministic v1 rules. Every result carries metrics and source row identifiers."""
import collections
import datetime as dt
import json
from engine.config import ROOT, read_json, page_path, write_json
from engine.collect.freshness import check
from engine.store.db import connect, upsert

RULES = ('striking_distance', 'low_ctr', 'no_impressions', 'two_pages_one_query', 'cluster_traction',
         'not_in_yandex_index', 'ai_robot_demand', 'robot_404', 'tech_issues', 'stale_source',
         'agent_trust_page', 'new_bot_family')


def cohort(published, end):
    if not published:
        return 'unknown'
    age = (dt.date.fromisoformat(end)-dt.date.fromisoformat(published)).days
    return 'after window' if age < 0 else '0–6' if age < 7 else '7–13' if age < 14 else '14–27' if age < 28 else '28+'


def window_rows(db, table, asof, source='gsc'):
    latest = db.execute(f"SELECT start,end FROM {table} WHERE source=? AND grain='window' AND end<=? ORDER BY end DESC,start DESC LIMIT 1", (source, asof)).fetchone()
    if not latest:
        return [], None
    rows = [dict(r) for r in db.execute(f"SELECT rowid AS row_id,* FROM {table} WHERE source=? AND grain='window' AND start=? AND end=?", (source, *latest))]
    return rows, list(latest)


def cohorts(db, cfg, date):
    rows, window = window_rows(db, 'page_day', date)
    if not window:
        return [], None
    impressions = {r['page']: r['impressions'] for r in rows}
    groups = {k: {'cohort': k, 'pages': 0, 'with_impressions': 0, 'without_returned_impressions': 0, 'share_eligible': None} for k in ('28+', '14–27', '7–13', '0–6', 'after window', 'unknown')}
    for p in db.execute('SELECT * FROM page WHERE current_crawl<=?', (date,)):
        if not p['path'].startswith(cfg['article_section']) or p['path'] == cfg['article_section']:
            continue
        g = groups[cohort(p['published'], window[1])]
        g['pages'] += 1
        g['with_impressions'] += impressions.get(p['path'], 0) > 0
    for key, g in groups.items():
        g['without_returned_impressions'] = g['pages']-g['with_impressions']
        # All percentage/share calculations use only the age-eligible denominator.
        if key == '28+' and g['pages']:
            g['share_eligible'] = g['without_returned_impressions']/g['pages']
    return list(groups.values()), window


def freshness(db, date):
    result = []
    for source in [r[0] for r in db.execute('SELECT DISTINCT source FROM stat_registry WHERE snapshot<=?', (date,))]:
        rows = db.execute('SELECT * FROM stat_registry WHERE source=? AND snapshot<=? ORDER BY newest_real DESC,snapshot DESC', (source, date)).fetchall()
        r = rows[0]
        result.append(dict(source=source, **check(r['newest_real'], date), evidence=json.loads(r['evidence'])))
    expected = ('gsc', 'yandex', 'metrika', 'access_log', 'funnel', 'site-digest', 'crawl')
    found = {r['source'] for r in result}
    for source in expected:
        if source not in found:
            result.append(dict(source=source, **check(None, date), evidence='missing source'))
    return sorted(result, key=lambda r: r['source'])


def run(cfg, date):
    db = connect(cfg)
    thresholds = cfg.get('signal_thresholds', {})
    minimum = thresholds.get('min_impressions', 1)
    ordinary_bots = set(read_json(ROOT/'playbooks/non_ai_bots.json'))
    p_rows, window = window_rows(db, 'page_day', date)
    q_rows, _ = window_rows(db, 'query_day', date)
    def emit(rule, subject, score, numbers, sources):
        upsert(db, 'signal', date=date, rule=rule, subject=subject, score=float(score),
               numbers=json.dumps(numbers, ensure_ascii=False), source_rows=json.dumps(sources, ensure_ascii=False))
    with db:
        db.execute('DELETE FROM signal WHERE date=?', (date,))
        for row in p_rows:
            imp, clicks, pos = row['impressions'], row['clicks'], row['position']
            refs = [{'table': 'page_day', 'rowid': row['row_id'], 'raw': row['evidence']}]
            nums = {'impressions': imp, 'clicks': clicks, 'position': pos, 'window': window, 'source': 'gsc'}
            if imp >= minimum and pos is not None:
                if 8 <= pos <= 20:
                    emit('striking_distance', row['page'], imp*(21-pos), nums, refs)
                if pos <= 10 and clicks/imp < thresholds.get('low_ctr', .02):
                    emit('low_ctr', row['page'], imp, dict(nums, ctr=clicks/imp), refs)
        if window:
            seen = {r['page'] for r in p_rows if r['impressions'] > 0}
            for p in db.execute('SELECT rowid AS row_id,* FROM page WHERE current_crawl<=?', (date,)):
                if p['path'].startswith(cfg['article_section']) and p['path'] != cfg['article_section'] and cohort(p['published'], window[1]) == '28+' and p['path'] not in seen:
                    emit('no_impressions', p['path'], 1, {'returned_impressions': 0, 'published': p['published'], 'window': window, 'caveat': 'No returned row is absence of observed impressions, not a measured zero; GSC reporting limits apply.'},
                         [{'table': 'page', 'rowid': p['row_id']}, {'table': 'page_day', 'source': 'gsc', 'window': window}])
        queries = collections.defaultdict(list)
        for r in q_rows:
            if r['page'] and r['impressions'] >= minimum:
                queries[r['query']].append(r)
        for query, rows in queries.items():
            if len({r['page'] for r in rows}) > 1:
                emit('two_pages_one_query', query, sum(r['impressions'] for r in rows), {'pages': [r['page'] for r in rows], 'impressions': sum(r['impressions'] for r in rows), 'window': window}, [{'table': 'query_day', 'rowid': r['row_id']} for r in rows])
        clusters = collections.defaultdict(list)
        for r in p_rows:
            p = db.execute('SELECT cluster,section FROM page WHERE path=?', (r['page'],)).fetchone()
            if p and r['impressions'] > 0:
                clusters[p['cluster'] or p['section']].append(r)
        # Traction means observed demand, not growth; section fallback is explicitly labelled.
        for cluster, rows in clusters.items():
            emit('cluster_traction', cluster, sum(r['impressions'] for r in rows), {'pages': len(rows), 'impressions': sum(r['impressions'] for r in rows), 'clicks': sum(r['clicks'] for r in rows), 'window': window, 'definition': 'observed demand; no growth inference'}, [{'table': 'page_day', 'rowid': r['row_id']} for r in rows])
        # Only explicit exclusion statuses prove not indexed. Missing samples are not exclusions.
        ix_date = db.execute('SELECT max(date) FROM index_day WHERE date<=?', (date,)).fetchone()[0]
        if ix_date:
            for r in db.execute("SELECT rowid AS row_id,* FROM index_day WHERE date=? AND state IN ('NOT_INDEXED','EXCLUDED','NOT_IN_SEARCH')", (ix_date,)):
                p = db.execute('SELECT published FROM page WHERE path=?', (r['page'],)).fetchone()
                if p and p['published'] and p['published'] <= ix_date:
                    emit('not_in_yandex_index', r['page'], 1, {'state': r['state'], 'observation': ix_date}, [{'table': 'index_day', 'rowid': r['row_id']}])
        start = (dt.date.fromisoformat(date)-dt.timedelta(days=6)).isoformat()
        bots = [dict(r) for r in db.execute('SELECT rowid AS row_id,* FROM bot_day WHERE date BETWEEN ? AND ?', (start, date))]
        demand, trust, notfound = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
        for r in bots:
            if r['family'] not in ordinary_bots and r['class'] in ('training crawler', 'search index', 'user-triggered fetch', 'coding agents') and r['verified'] != 'spoofed':
                demand[r['page']].append(r)
            if r['class'] == 'user-triggered fetch' and r['verified'] == 'verified':
                trust[r['page']].append(r)
            if r['status'] == 404:
                notfound[r['page']].append(r)
        for rule, mapping in [('ai_robot_demand', demand), ('agent_trust_page', trust), ('robot_404', notfound)]:
            for page, rows in mapping.items():
                n = sum(r['hits'] for r in rows)
                emit(rule, page, n, {'requests': n, 'verified_requests': sum(r['hits'] for r in rows if r['verified'] == 'verified'), 'families': sorted({r['family'] for r in rows}), 'window': [start, date]}, [{'table': 'bot_day', 'rowid': r['row_id']} for r in rows])
        panel = collections.defaultdict(list)
        for r in db.execute('SELECT rowid AS row_id,* FROM ai_panel WHERE date BETWEEN ? AND ?', (start, date)):
            for url in json.loads(r['cited_urls']):
                from urllib.parse import urlsplit
                if urlsplit(url).netloc == urlsplit(cfg['domain']).netloc:
                    panel[page_path(url)].append(r['row_id'])
        for page, ids in panel.items():
            existing = db.execute("SELECT * FROM signal WHERE date=? AND rule='agent_trust_page' AND subject=?", (date, page)).fetchone()
            nums = json.loads(existing['numbers']) if existing else {'requests': 0, 'window': [start, date]}
            refs = json.loads(existing['source_rows']) if existing else []
            emit('agent_trust_page', page, nums['requests']+len(ids), dict(nums, panel_citations=len(ids)), refs+[{'table': 'ai_panel', 'rowid': i} for i in ids])
        families = collections.defaultdict(list)
        for r in bots:
            if r['family'].startswith('unknown:'):
                families[r['family']].append(r)
        for family, rows in families.items():
            daily = collections.Counter()
            for r in rows:
                daily[r['date']] += r['hits']
            days = sorted(daily)
            latest = daily[days[-1]]
            baseline = sum(daily[d] for d in days[:-1])/max(1, len(days)-1)
            if len(days) >= 2 and latest >= thresholds.get('new_bot_min_hits', 10) and latest > baseline*1.5:
                emit('new_bot_family', family, latest, {'daily': dict(daily), 'latest_hits': latest, 'baseline_daily_mean': baseline, 'window': [start, date]}, [{'table': 'bot_day', 'rowid': r['row_id']} for r in rows])
        crawl_date = db.execute('SELECT max(date) FROM crawl_page WHERE date<=?', (date,)).fetchone()[0]
        for r in db.execute('SELECT rowid AS row_id,* FROM crawl_page WHERE date=?', (crawl_date,)):
            issues = json.loads(r['issues'])
            if issues:
                emit('tech_issues', r['page'], len(issues), {'issues': issues, 'words': r['words'], 'crawl_date': crawl_date}, [{'table': 'crawl_page', 'rowid': r['row_id'], 'raw': r['evidence']}])
        for r in freshness(db, date):
            if r['status'] == 'STALE':
                emit('stale_source', r['source'], r['age_days'] or 999, r, [{'table': 'stat_registry', 'source': r['source'], 'evidence': r['evidence']}])
    result = {rule: db.execute('SELECT count(*) FROM signal WHERE date=? AND rule=?', (date, rule)).fetchone()[0] for rule in RULES}
    write_json(cfg['data']/'signals'/ (date+'.json'), [dict(r) for r in db.execute('SELECT * FROM signal WHERE date=? ORDER BY score DESC,rule,subject', (date,))])
    db.close()
    return result
