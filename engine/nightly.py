"""Run all local stages even when a collector is unavailable; return a loud failure.
No deployment, notification or external write is performed.
"""
import argparse
import datetime as dt
import json
from engine.config import load
from engine.collect.run import collect
from engine.crawl.run import crawl
from engine.store.load import load as load_data
from engine.signals.run import run
from engine.report.run import report
from engine.proposals.run import propose, card


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
    api_failure = bool(result['collect'].get('errors'))
    return 2 if result['signals']['stale_source'] else 1 if api_failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
