"""Configuration uses JSON, a deliberately small valid YAML subset."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def load(site):
    path = Path(site) if str(site).endswith(('.yaml', '.json')) else ROOT / 'sites' / site / 'site.yaml'
    cfg = read_json(path)
    cfg['data'] = Path(os.environ.get('SEO_ENGINE_DATA', cfg['data_dir'])).expanduser().resolve()
    cfg['data'].mkdir(parents=True, exist_ok=True)
    return cfg


def page_path(value):
    p = unquote(urlsplit(value).path) or '/'
    if p.endswith('/index.html'):
        p = p[:-10]
    if not Path(p).suffix and not p.endswith('/'):
        p += '/'
    return p


def section(cfg, path):
    return next((s for s in sorted(cfg['sections'], key=len, reverse=True)
                 if s != '/' and path.startswith(s)), '/' if path == '/' else 'other')


def disk_file(root, url):
    root = Path(root).expanduser().resolve()
    p = page_path(url)
    target = (root / p.lstrip('/') / 'index.html' if p.endswith('/') else root / p.lstrip('/')).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Path outside configured site root')
    return target
