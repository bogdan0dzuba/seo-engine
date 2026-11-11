"""Snapshot loaders preserve source windows and provenance, never sum snapshots."""
import collections
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from engine.config import read_json, page_path, section
from engine.collect.freshness import iso, real_dates, yandex_evidence, check, month_end
from engine.collect.access_log import assistant
from engine.store.db import connect, upsert, counts

J = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True)


def evidence(file, row=None):
    return str(file)+(':'+str(row) if row is not None else '')


def register(db, source, snapshot, newest, ev, asof):
    upsert(db, 'stat_registry', source=source, snapshot=snapshot, newest_real=newest,
           status=check(newest, asof)['status'], evidence=J(ev))


def gsc_window(payloads, snapshot):
    # Old collector did not save the requested bounds: reconstruct from its documented
    # fixed 28-day lag-3 contract, not min/max observed dates (which may have gaps).
    end = dt.date.fromisoformat(snapshot)-dt.timedelta(days=3)
    return (end-dt.timedelta(days=27)).isoformat(), end.isoformat()


def search_row(db, table, source, start, end, page, query, grain, row, ev):
    values = dict(source=source, start=start, end=end, page=page, grain=grain,
                  impressions=row.get('impressions', 0), clicks=row.get('clicks', 0),
                  position=row.get('position'), evidence=ev)
    if table == 'query_day':
        values['query'] = query
    else:
        values.update(dimension='', visits=None)
    upsert(db, table, **values)


def load_gsc(db, payloads, folder):
    start, end = gsc_window(payloads, folder.name)
    for name in ('gsc_page', 'gsc_query', 'gsc_page_query', 'gsc_date', 'gsc_date_page_query'):
        payload = payloads.get(name, {})
        bounds = payload.get('_window', [start, end])
        for i, row in enumerate(payload.get('rows', [])):
            keys = row['keys']
            a, b, page, query, grain = *bounds, '', '', 'window'
            if name == 'gsc_date':
                a = b = keys[0]
                grain = 'day'
                page = '*'
            elif name == 'gsc_date_page_query':
                a = b = keys[0]
                page, query, grain = page_path(keys[1]), keys[2], 'day'
            elif name == 'gsc_page':
                page = page_path(keys[0])
            elif name == 'gsc_query':
                query = keys[0]
            else:
                page, query = page_path(keys[0]), keys[1]
            search_row(db, 'query_day' if 'query' in name else 'page_day', 'gsc', a, b, page, query, grain, row, evidence(folder/(name+'.json'), i))
    dates = [r['keys'][0] for r in payloads.get('gsc_date', {}).get('rows', []) if r.get('impressions', 0) or r.get('clicks', 0)]
    if 'gsc_date' in payloads:
        register(db, 'gsc', folder.name, max(dates, default=None), str(folder/'gsc_date.json'), folder.name)


def load_wm(db, payloads, folder):
    for name in ('wm_analytics_url', 'wm_analytics_query'):
        for pi, batch in enumerate(payloads.get(name, [])):
            if batch.get('status') != 200:
                continue
            for ri, row in enumerate(batch.get('payload', {}).get('text_indicator_to_statistics', [])):
                days = collections.defaultdict(dict)
                for stat in row.get('statistics', []):
                    days[stat['date']][stat['field']] = stat['value']
                for date, stats in days.items():
                    value = row['text_indicator']['value']
                    # Complementary indicator is only the most popular counterpart,
                    # NOT a joint query×page measurement. Never invent a query→page join.
                    page, query = (page_path(value), '') if name.endswith('_url') else ('', value)
                    search_row(db, 'page_day' if name.endswith('_url') else 'query_day', 'yandex', date, date, page, query, 'day',
                               {'impressions': stats.get('IMPRESSIONS', 0), 'clicks': stats.get('CLICKS', 0), 'position': stats.get('POSITION')},
                               evidence(folder/(name+'.json'), f'{pi}/{ri}/{date}'))
    if any(n.startswith('wm_') for n in payloads):
        ev = yandex_evidence(payloads)
        newest = max((d for d in ev.values() if d), default=None)
        register(db, 'yandex', folder.name, newest, ev, folder.name)
    samples = payloads.get('wm_in_search_samples', {})
    for i, row in enumerate(samples.get('samples', [])):
        upsert(db, 'index_day', date=folder.name, page=page_path(row['url']), state='in_search', source='yandex', evidence=evidence(folder/'wm_in_search_samples.json', i))
    for i, row in enumerate(payloads.get('wm_indexing_samples', {}).get('samples', [])):
        upsert(db, 'index_day', date=folder.name, page=page_path(row['url']), state=row.get('status', 'unknown'), source='yandex_indexing', evidence=evidence(folder/'wm_indexing_samples.json', i))
    for key in ('wm_in_search_history', 'wm_indexing_history', 'wm_sitemaps', 'wm_diagnostics', 'wm_summary', 'wm_index_check'):
        if key in payloads:
            upsert(db, 'metric_day', source='yandex', start=folder.name, end=folder.name, dimension=key, key='all', payload=J(payloads[key]), evidence=evidence(folder/(key+'.json')))


def load_metrika(db, payloads, folder, cfg):
    newest = []
    ai_referrals = {}
    for name, p in payloads.items():
        if not name.startswith('ym_'):
            continue
        q = p.get('query', {})
        dims, metrics = q.get('dimensions', []), q.get('metrics', [])
        for i, r in enumerate(p.get('data', [])):
            d = {key: v.get('name', '') for key, v in zip(dims, r.get('dimensions', []))}
            m = dict(zip(metrics, r.get('metrics', [])))
            date = iso(d.get('ym:s:date'))
            if date and any(r.get('metrics', [])):
                newest.append(date)
            a, b = (date, date) if date else (q['date1'], q['date2'])
            ev = evidence(folder/(name+'.json'), i)
            upsert(db, 'metric_day', source='metrika', start=a, end=b, dimension=name, key=J(d), payload=J(m), evidence=ev)
            if 'ym:s:startURL' in d:
                path = page_path(d['ym:s:startURL'])
                upsert(db, 'page_day', source='metrika', start=a, end=b, page=path, dimension=J(d), grain='day' if date else 'window', impressions=None, clicks=None, position=None, visits=m.get('ym:s:visits', 0), evidence=ev)
            if date and name == 'ym_referrers':
                domain = assistant(d.get('ym:s:referer', ''), cfg['ai_referrers'])
                if domain:
                    key = (date, domain, page_path(d.get('ym:s:startURL', '/')))
                    entry = ai_referrals.setdefault(key, {'visits': 0, 'evidence': []})
                    entry['visits'] += m.get('ym:s:visits', 0)
                    entry['evidence'].append(ev)
    for (date, domain, page), entry in ai_referrals.items():
        upsert(db, 'ai_visibility', date=date, source='metrika', assistant=domain, page=page, requests=entry['visits'], evidence=J(entry['evidence']))
    if 'ym_daily' in payloads:
        register(db, 'metrika', folder.name, max(newest, default=None), str(folder/'ym_daily.json'), folder.name)


def load_extra(db, payloads, folder, cfg):
    for name in ('funnel', 'site-digest'):
        if name not in payloads:
            continue
        p = payloads[name]
        ev = evidence(folder/(name+'.json'))
        upsert(db, 'metric_day', source=name, start=folder.name, end=folder.name, dimension='snapshot', key='all', payload=J(p), evidence=ev)
        if name == 'funnel':
            windows = p.get('windows', {})
            real = [dt.date.fromisoformat(w['to_exclusive'])-dt.timedelta(days=1) for w in windows.values() if w.get('totals', {}).get('visits', 0) > 0]
            newest = max(real).isoformat() if real else None
        else:
            newest = iso(p.get('date')) if p.get('top_pages') else None
        register(db, name, folder.name, newest, ev, folder.name)
    for name, p in payloads.items():
        if not name.startswith('wordstat_'):
            continue
        ev = evidence(folder/(name+'.json'))
        upsert(db, 'metric_day', source='wordstat', start=p.get('month', ''), end=p.get('date', ''), dimension=name, key='all', payload=J(p), evidence=ev)
        real = month_end(p.get('month')) if any(r.get('count', 0) > 0 for r in p.get('items', [])) else None
        register(db, name, folder.name, real, {'path': ev, 'month': p.get('month'), 'errors': p.get('errors', [])}, folder.name)
        for item in p.get('items', []):
            for url in item.get('pages', []):
                path = page_path(url)
                db.execute('UPDATE page SET cluster=? WHERE path=?', (item.get('cluster'), path))
    for row in payloads.get('access_log', {}).get('requests', []):
        upsert(db, 'log_request', **row)
    requests = payloads.get('access_log', {}).get('requests', [])
    if 'access_log' in payloads:
        register(db, 'access_log', folder.name, max((r['date'] for r in requests), default=None), str(folder/'access_log.json'), folder.name)
    for row in payloads.get('ai_panel', []):
        upsert(db, 'ai_panel', date=row['date'], model=row['model'], question_id=row['question_id'], mentioned=int(row['mentioned']),
               cited_urls=J(row['cited_urls']), competitor_names=J(row['competitor_names']), offer_phrase_found=int(row['offer_phrase_found']), raw_answer_path=row['raw_answer_path'])
    for i, r in enumerate(payloads.get('crawl', [])):
        ev = evidence(folder/'crawl.json', i)
        upsert(db, 'page', path=r['page'], section=r['section'], published=r['published'], cluster=None, current_crawl=folder.name)
        upsert(db, 'crawl_page', date=folder.name, page=r['page'], title=r['title'], description=r['description'], h1=J(r['h1']), canonical=J(r['canonical']),
               jsonld_types=J(r['jsonld_types']), words=r['words'], published=r['published'], links=J(r['links']), sha256=r['sha256'], issues=J(r['issues']), evidence=ev)
    if 'crawl' in payloads:
        register(db, 'crawl', folder.name, folder.name, str(folder/'crawl.json'), folder.name)


def aggregate_logs(db):
    # Refresh from durable fingerprints while preserving rowids used in provenance.
    rows = db.execute("""SELECT date,family,class,verified,page,status,count(*) AS hits,
                         max(special) AS special,min(evidence) AS evidence FROM log_request
                         WHERE family!='other' GROUP BY date,family,class,verified,page,status""").fetchall()
    for row in rows:
        upsert(db, 'bot_day', **dict(row))
    db.execute("""DELETE FROM bot_day WHERE NOT EXISTS (SELECT 1 FROM log_request r
                  WHERE r.date=bot_day.date AND r.family=bot_day.family AND r.class=bot_day.class
                  AND r.verified=bot_day.verified AND r.page=bot_day.page AND r.status=bot_day.status)""")
    ai_rows = db.execute("""SELECT date,'access_log' AS source,assistant,page,count(*) AS requests,
                            min(evidence) AS evidence FROM log_request WHERE assistant!=''
                            GROUP BY date,assistant,page""").fetchall()
    for row in ai_rows:
        upsert(db, 'ai_visibility', **dict(row))
    db.execute("""DELETE FROM ai_visibility WHERE source='access_log' AND NOT EXISTS
                  (SELECT 1 FROM log_request r WHERE r.date=ai_visibility.date
                   AND r.assistant=ai_visibility.assistant AND r.page=ai_visibility.page)""")


def load(cfg, date):
    db = connect(cfg)
    with db:
        for folder in sorted(p for p in (cfg['data']/'raw').glob('????-??-??') if p.name <= date):
            payloads = {}
            for f in sorted(folder.glob('*.json')):
                payloads[f.stem] = read_json(f)
                upsert(db, 'source_snapshot', snapshot=folder.name, name=f.stem, sha256=hashlib.sha256(f.read_bytes()).hexdigest(), path=str(f))
            # Crawl first so cluster assignments survive extra imports.
            only_crawl = {'crawl': payloads['crawl']} if 'crawl' in payloads else {}
            load_extra(db, only_crawl, folder, cfg)
            load_gsc(db, payloads, folder)
            load_wm(db, payloads, folder)
            load_metrika(db, payloads, folder, cfg)
            load_extra(db, {k: v for k, v in payloads.items() if k != 'crawl'}, folder, cfg)
        f = Path(cfg.get('effects_file', '')).expanduser()
        if f.is_file():
            text = f.read_text()
            for match in re.finditer(r'^## (E-\d+) · (\d{4}-\d{2}-\d{2})[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S):
                ident, day, record = match.groups()
                upsert(db, 'experiment', id=ident, date=day, status='unassessed', record=record.strip(), source=str(f))
        aggregate_logs(db)
    result = counts(db)
    db.close()
    return result
