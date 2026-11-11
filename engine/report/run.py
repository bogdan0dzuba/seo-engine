import datetime as dt
import html
import json
from engine.signals.run import cohorts, freshness, window_rows, RULES
from engine.store.db import connect, counts
from engine.config import ROOT, read_json, write_json, section


def table(rows):
    if not rows:
        return '<p>No rows.</p>'
    keys = list(rows[0])
    def escape(v):
        return html.escape(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
    return '<table border="1"><thead><tr>'+''.join('<th>'+escape(k)+'</th>' for k in keys)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(r.get(k, ''))+'</td>' for k in keys)+'</tr>' for r in rows)+'</tbody></table>'


def changes(db, cfg, date):
    windows = db.execute("SELECT DISTINCT start,end FROM page_day WHERE source='gsc' AND grain='window' AND end<=? ORDER BY end DESC,start DESC LIMIT 2", (date,)).fetchall()
    if len(windows) < 2:
        return {'available': False, 'note': 'В хранилище только одно окно GSC; сравнение с предыдущим недоступно.' if windows else 'Окна GSC отсутствуют; сравнение недоступно.', 'sections': [], 'pages': []}
    current, previous = [list(w) for w in windows]
    def values(window):
        return {r['page']: dict(r) for r in db.execute("SELECT page,impressions,clicks FROM page_day WHERE source='gsc' AND grain='window' AND start=? AND end=? AND dimension=''", window)}
    now, before = values(current), values(previous)
    pages, sections = [], {s: dict(section=s, clicks_previous=0, clicks_current=0, clicks_change=0, impressions_previous=0, impressions_current=0, impressions_change=0) for s in cfg['sections']}
    for page in sorted(now.keys() | before.keys()):
        r = {'page': page}
        sec = section(cfg, page)
        total = sections.setdefault(sec, {'section': sec})
        for metric in ('clicks', 'impressions'):
            a, b = now.get(page, {}).get(metric, 0), before.get(page, {}).get(metric, 0)
            for key, value in ((metric+'_previous', b), (metric+'_current', a), (metric+'_change', a-b)):
                r[key] = value
                total[key] = total.get(key, 0)+value
        r['row_presence'] = 'both' if page in now and page in before else 'current only' if page in now else 'previous only'
        pages.append(r)
    pages.sort(key=lambda r: (-abs(r['clicks_change']), -abs(r['impressions_change']), r['page']))
    return {'available': True, 'current_window': current, 'previous_window': previous,
            'note': 'Два последних окна GSC сравниваются отдельно, без сложения. Окна могут пересекаться. Top 10 по абсолютному изменению кликов, затем показов; отсутствие строки даёт 0 возвращённых показов, а не измеренный ноль.',
            'sections': [sections[k] for k in sorted(sections)], 'pages': [r for r in pages if r['clicks_change'] or r['impressions_change']][:10]}


def verification_counts(db, date):
    start = (dt.date.fromisoformat(date)-dt.timedelta(days=6)).isoformat()
    families = {family: dict(family=family, verified=0, unverified=0, unavailable=0, spoofed=0)
                for family in sorted({n for names in read_json(ROOT/'playbooks/ai_bots.yaml').values() for n in names})}
    for row in db.execute('SELECT family,verified,sum(hits) AS hits FROM bot_day WHERE date BETWEEN ? AND ? GROUP BY family,verified', (start, date)):
        family = families.setdefault(row['family'], dict(family=row['family'], verified=0, unverified=0, unavailable=0, spoofed=0))
        family[row['verified']] += row['hits']
    return {'window': [start, date], 'families': list(families.values())}


def report(cfg, date):
    db = connect(cfg)
    cohort_rows, window = cohorts(db, cfg, date)
    delta = changes(db, cfg, date)
    verification = verification_counts(db, date)
    fresh = freshness(db, date, cfg)
    signals = [dict(r) for r in db.execute('SELECT * FROM signal WHERE date=? ORDER BY score DESC,rule,subject', (date,))]
    by_rule = [{'rule': rule, 'count': sum(r['rule'] == rule for r in signals)} for rule in RULES]
    pages, _ = window_rows(db, 'page_day', date)
    summary = {'asof': date, 'search_source': 'GSC', 'search_window': window, 'search_pages_returned': len(pages),
               'impressions': sum(r['impressions'] for r in pages), 'clicks': sum(r['clicks'] for r in pages),
               'crawl_pages': db.execute('SELECT count(*) FROM page WHERE current_crawl<=?', (date,)).fetchone()[0],
               'signals': len(signals)}
    notes = ('GSC window aggregates are never added across snapshots. Cohorts use publication age at END of GSC window. '
             'Only 28+ cohort is eligible for the no-impressions share/signal; other cohorts show counts only. '
             'Absent GSC rows mean no returned impressions, not measured zeros. Unknown publication dates are excluded. '
             'No absence-from-index conclusion is drawn from incomplete samples. '
             'IP identity uses the nearest publisher snapshot within ±30 days; unavailable means no published ranges. UA claims alone are not verified. HTTP requests are not visits or people. '
             'Wordstat freshness uses the measured month, not export date. Reports never send or edit sites.')
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Контур — '+date+'</title>',
             '<body><h1>Контур · '+html.escape(cfg['domain'])+' · '+date+'</h1>', table([summary]), '<p>'+notes+'</p>',
             '<h2>Article age cohorts</h2>', table(cohort_rows), '<h2>Data freshness (7-day limit)</h2>', table(fresh),
             '<h2>Change against the previous window / Изменение к предыдущему окну</h2>', '<p>'+html.escape(delta['note'])+'</p>', table([{k: v for k, v in delta.items() if k.endswith('_window')}]) if delta['available'] else '', table(delta['sections']), table(delta['pages']),
             '<h2>Robot verification</h2>', table([{'window': verification['window']}]), table(verification['families']), '<h2>Signal counts (top 10 per rule below)</h2>', table(by_rule)]
    # Show top rows of every rule, so high-volume robot scores do not hide search opportunities.
    for rule in RULES:
        rows = [dict(subject=r['subject'], score=r['score'], numbers=json.loads(r['numbers']), source_rows=json.loads(r['source_rows'])) for r in signals if r['rule'] == rule][:10]
        parts.extend(['<h2>'+rule+'</h2>', table(rows)])
    parts.append('</body></html>')
    day = dt.date.fromisoformat(date)
    week = (day-dt.timedelta(days=day.weekday())).isoformat()
    out = cfg['data']/'reports'
    out.mkdir(parents=True, exist_ok=True)
    path = out/(week+'.html')
    path.write_text('\n'.join(parts), encoding='utf-8')
    change_summary = delta['note'] if not delta['available'] else str(delta['previous_window'])+' → '+str(delta['current_window'])+'; clicks Δ='+str(sum(r['clicks_change'] for r in delta['sections']))+'; impressions Δ='+str(sum(r['impressions_change'] for r in delta['sections']))+'; разделы и top 10 страниц — в HTML/JSON.'
    robot_summary = {status: sum(r[status] for r in verification['families']) for status in ('verified', 'unverified', 'unavailable', 'spoofed')}
    digest = '\n'.join([f"Контур {date}: {cfg['domain']}", f'GSC window: {window}; impressions {summary["impressions"]}; clicks {summary["clicks"]}.',
                       'Signals: '+', '.join(r['rule']+'='+str(r['count']) for r in by_rule),
                       'STALE: '+', '.join(r['source']+' ('+str(r['newest_real'])+')' for r in fresh if r['status'] == 'STALE'),
                       'Изменение к предыдущему окну: '+change_summary,
                       'Роботы '+str(verification['window'])+': '+json.dumps(robot_summary, ensure_ascii=False)+'; по семействам — в HTML/JSON.',
                       notes])
    (out/(week+'.txt')).write_text(digest+'\n', encoding='utf-8')
    write_json(out/(week+'.json'), {'summary': summary, 'cohorts': cohort_rows, 'freshness': fresh, 'signals': by_rule, 'counts': counts(db), 'changes': delta, 'robot_verification': verification})
    db.close()
    return {'html': str(path), 'digest': str(out/(week+'.txt'))}
