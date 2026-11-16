"""Run all local stages even when a collector is unavailable.
Exit code: 0 when Yandex and Google each delivered at least one endpoint (optional endpoint
errors and stale data are only reported: stdout JSON, stderr, *_manifest.json errors[]/stale);
3 on an authorization error; 1 when a source failed completely. Stale data never changes the code.
No deployment, notification or external write is performed.
"""
import argparse
import datetime as dt
import json
import sys
from engine.config import load, read_json
from engine.collect.run import collect
from engine.crawl.run import crawl
from engine.store.load import load as load_data
from engine.signals.run import run
from engine.report.run import report
from engine.proposals.run import propose, card


def exit_code(cfg, date, collect_result):
    """Non-zero only for an authorization error or a source with no working endpoint at all."""
    code = 0
    for source in ('yandex', 'google'):
        outcome = collect_result.get(source)
        if outcome == 'disabled' or str(outcome).startswith('saved snapshots'):
            continue
        try:
            manifest = read_json(cfg['data']/'raw'/date/(source+'_manifest.json'))
        except (OSError, ValueError):
            manifest = {}
        if outcome == 'failed' or manifest.get('auth_failed'):
            code = max(code, 3 if manifest.get('auth_failed') else 1)
        elif manifest.get('attempted') and not manifest.get('ok'):
            code = max(code, 1)
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--site', required=True)
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--live', action='store_true')
    a = ap.parse_args()
    cfg = load(a.site)
    result = {'collect': collect(cfg, a.date, live=a.live)}
    result['crawl'] = crawl(cfg, a.date)
    result['load'] = load_data(cfg, a.date)
    result['signals'] = run(cfg, a.date)
    result['report'] = report(cfg, a.date)
    result['propose'] = propose(cfg, a.date)
    result['card'] = card(cfg, a.date)
    print(json.dumps(result, ensure_ascii=False))
    code = exit_code(cfg, a.date, result['collect'])
    if result['collect'].get('errors'):
        print('COLLECT ERRORS (optional endpoints, run continues): '+json.dumps(result['collect']['errors']), file=sys.stderr)
    if result['signals']['stale_source']:
        print('STALE sources: '+str(result['signals']['stale_source'])+' (see signals/report)', file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
