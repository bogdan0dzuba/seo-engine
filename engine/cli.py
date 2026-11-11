"""python3 -m engine.cli COMMAND --site NAME [--date YYYY-MM-DD]."""
import argparse
import datetime as dt
import json
import sys
from engine.config import load


def main(argv=None):
    parser = argparse.ArgumentParser(description='Контур: read-only SEO measurement engine')
    parser.add_argument('command', choices=['collect', 'crawl', 'load', 'signals', 'report', 'ip-ranges'])
    parser.add_argument('--site', required=True, help='site name or config path')
    parser.add_argument('--date', default=dt.date.today().isoformat())
    parser.add_argument('--live', action='store_true', help='read live APIs using Keychain; never implied')
    parser.add_argument('--backfill', action='store_true')
    parser.add_argument('--start')
    parser.add_argument('--end')
    args = parser.parse_args(argv)
    dt.date.fromisoformat(args.date)
    cfg = load(args.site)
    if args.command == 'collect':
        from engine.collect.run import collect
        result = collect(cfg, args.date, args.live, args.backfill, args.start, args.end)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if args.live and any(result.get(s) != 'live' for s in ('google', 'yandex')) else 0
    if args.command == 'crawl':
        from engine.crawl.run import crawl
        result = crawl(cfg, args.date)
    elif args.command == 'load':
        from engine.store.load import load as load_data
        result = load_data(cfg, args.date)
    elif args.command == 'signals':
        from engine.signals.run import run, freshness
        from engine.store.db import connect
        result = run(cfg, args.date)
        print(json.dumps(result, ensure_ascii=False))
        if result['stale_source']:
            db = connect(cfg)
            for source in freshness(db, args.date, cfg):
                if source['status'] == 'STALE':
                    print('STALE: '+source['source']+' newest_real='+str(source['newest_real']), file=sys.stderr)
            db.close()
            return 2
        return 0
    elif args.command == 'report':
        from engine.report.run import report
        result = report(cfg, args.date)
    else:
        from engine.collect.access_log import fetch_ranges
        fetch_ranges(args.date)
        result = {'date': args.date, 'ip_ranges': 'saved'}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
