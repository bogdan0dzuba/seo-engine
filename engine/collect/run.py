import datetime as dt
import shutil
from pathlib import Path
from engine.config import read_json, write_json
from engine.collect import access_log, google, yandex
from engine.collect.http import CollectionError


def saved(cfg, date, backfill=False):
    root = Path(cfg['snapshot_dir']).expanduser()
    days = sorted(p for p in root.glob('????-??-??') if cfg.get('backfill_start', '0000-00-00') <= p.name <= date) if backfill else [root/date]
    for source in days:
        dest = cfg['data']/'raw'/source.name
        dest.mkdir(parents=True, exist_ok=True)
        for f in source.glob('*.json'):
            if not (dest/f.name).exists():
                shutil.copyfile(f, dest/f.name)
    evidence = Path(cfg.get('index_evidence', '')).expanduser()
    if evidence.is_file() and cfg.get('index_evidence_date', date) <= date:
        dest = cfg['data']/'raw'/cfg.get('index_evidence_date', date)/'wm_index_check.json'
        if not dest.exists():
            write_json(dest, read_json(evidence))


def collect(cfg, date, live=False, backfill=False, start=None, end=None):
    if cfg.get('snapshot_dir'):
        saved(cfg, date, backfill)
    folder = cfg['data']/'raw'/date
    folder.mkdir(parents=True, exist_ok=True)
    outcomes = {}
    if live:
        end = end or (dt.date.fromisoformat(date)-dt.timedelta(days=3)).isoformat()
        start = start or (dt.date.fromisoformat(end)-dt.timedelta(days=27)).isoformat()
        if start > end:
            raise ValueError('start after end')
        for name, module in [('yandex', yandex), ('google', google)]:
            try:
                module.collect(cfg, folder, start, end)
                outcomes[name] = 'live'
            except CollectionError as e:
                outcomes[name] = str(e)
                # No retry / alternate credential route after a guard or network error.
    else:
        outcomes = {'yandex': 'saved snapshots; live not requested', 'google': 'saved snapshots; live not requested'}
    for key in ('funnel_digest', 'site_digest'):
        if key in cfg:
            f = Path(cfg[key].format(date=date)).expanduser()
            if f.is_file():
                write_json(folder/('funnel.json' if key == 'funnel_digest' else 'site-digest.json'), read_json(f))
    for i, name in enumerate(cfg.get('wordstat_files', [])):
        f = Path(name).expanduser()
        if f.is_file():
            write_json(folder/('wordstat_'+str(i)+'.json'), read_json(f))
    if cfg.get('access_log') and Path(cfg['access_log']).expanduser().is_file():
        outcomes['access_log'] = access_log.parse(cfg, folder/'access_log.json')
    write_json(folder/'collection.json', outcomes)
    return outcomes
