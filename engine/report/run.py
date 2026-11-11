import datetime as dt
import html
import json
from engine.signals.run import cohorts, freshness, window_rows, RULES
from engine.store.db import connect, counts
from engine.config import write_json


def table(rows):
    if not rows:
        return '<p>No rows.</p>'
    keys = list(rows[0])
    def escape(v):
        return html.escape(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
    return '<table border="1"><thead><tr>'+''.join('<th>'+escape(k)+'</th>' for k in keys)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(r.get(k, ''))+'</td>' for k in keys)+'</tr>' for r in rows)+'</tbody></table>'


def report(cfg, date):
    db = connect(cfg)
    cohort_rows, window = cohorts(db, cfg, date)
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
             'AI UA claims are unverified unless a dated publisher IP range matches. HTTP requests are not visits or people. '
             'Wordstat freshness uses the measured month, not export date. Reports never send or edit sites.')
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Контур — '+date+'</title>',
             '<body><h1>Контур · '+html.escape(cfg['domain'])+' · '+date+'</h1>', table([summary]), '<p>'+notes+'</p>',
             '<h2>Article age cohorts</h2>', table(cohort_rows), '<h2>Data freshness (7-day limit)</h2>', table(fresh),
             '<h2>Signal counts</h2>', table(by_rule)]
    # Show top rows of every rule, so high-volume robot scores do not hide search opportunities.
    for rule in RULES:
        rows = [dict(subject=r['subject'], score=r['score'], numbers=json.loads(r['numbers']), source_rows=json.loads(r['source_rows'])) for r in signals if r['rule'] == rule][:20]
        parts.extend(['<h2>'+rule+'</h2>', table(rows)])
    parts.append('</body></html>')
    day = dt.date.fromisoformat(date)
    week = (day-dt.timedelta(days=day.weekday())).isoformat()
    out = cfg['data']/'reports'
    out.mkdir(parents=True, exist_ok=True)
    path = out/(week+'.html')
    path.write_text('\n'.join(parts), encoding='utf-8')
    digest = '\n'.join([f"Контур {date}: {cfg['domain']}", f'GSC window: {window}; impressions {summary["impressions"]}; clicks {summary["clicks"]}.',
                       'Signals: '+', '.join(r['rule']+'='+str(r['count']) for r in by_rule),
                       'STALE: '+', '.join(r['source']+' ('+str(r['newest_real'])+')' for r in fresh if r['status'] == 'STALE'),
                       notes])
    (out/(week+'.txt')).write_text(digest+'\n', encoding='utf-8')
    write_json(out/(week+'.json'), {'summary': summary, 'cohorts': cohort_rows, 'freshness': fresh, 'signals': by_rule, 'counts': counts(db)})
    db.close()
    return {'html': str(path), 'digest': str(out/(week+'.txt'))}
