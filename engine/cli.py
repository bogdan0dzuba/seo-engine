"""python3 -m engine.cli COMMAND --site NAME [--date YYYY-MM-DD]."""
import argparse
import datetime as dt
import json
import sys
from engine.config import load


def main(argv=None):
    parser = argparse.ArgumentParser(description='Контур: read-only SEO measurement engine')
    parser.add_argument('command', choices=['collect', 'crawl', 'load', 'signals', 'report', 'ip-ranges', 'init-site', 'propose', 'card'])
    parser.add_argument('--site', required=True, help='site name or config path')
    parser.add_argument('--date', default=dt.date.today().isoformat())
    parser.add_argument('--live', action='store_true', help='read live APIs using Keychain; never implied')
    parser.add_argument('--backfill', action='store_true')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--domain')
    parser.add_argument('--data-dir')
    args = parser.parse_args(argv)
    dt.date.fromisoformat(args.date)
    if args.command == 'init-site':
        if not args.domain:
            parser.error('init-site requires --domain')
        from engine.config import init_site
        print(json.dumps(init_site(args.site, args.domain, args.data_dir), ensure_ascii=False))
        return 0
    cfg = load(args.site)
    if args.command == 'collect':
        from engine.collect.run import collect
        result = collect(cfg, args.date, args.live, args.backfill, args.start, args.end)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('errors') else 0
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
    elif args.command in ('propose', 'card'):
        from engine.proposals.run import propose, card
        result = (propose if args.command == 'propose' else card)(cfg, args.date)
    elif args.command == 'report':
        from engine.report.run import report
        result = report(cfg, args.date)
    else:
        from engine.collect.access_log import fetch_ranges
        errors = fetch_ranges(args.date)
        print(json.dumps({'date': args.date, 'ip_ranges': 'saved', 'errors': errors}))
        return 1 if errors else 0
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
