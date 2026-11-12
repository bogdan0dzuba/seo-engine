"""Configuration uses JSON, a deliberately small valid YAML subset."""
import json
import os
import re
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
    cfg.setdefault('article_section', '/blog/')
    cfg.setdefault('ai_referrers', [])
    cfg.setdefault('internal_user_agents', ['Headless', 'python-urllib', 'healthcheck'])
    cfg.setdefault('client_address_header', 'X-Forwarded-For')
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


def init_site(site, domain, data_dir=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', site):
        raise ValueError('Use a lowercase site name with letters, digits, - or _')
    url = urlsplit(domain)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('domain must be an HTTP(S) origin')
    folder = ROOT / 'sites' / site
    cfg = read_json(ROOT / 'sites' / '_template' / 'site.yaml')
    cfg.update(domain=domain.rstrip('/'), data_dir=data_dir or str(ROOT / '.data' / site))
    folder.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects an existing site's configuration.
    with (folder / 'site.yaml').open('x', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return {'config': str(folder / 'site.yaml')}


def enabled_sources(cfg):
    keys = cfg.get('keychain', {})
    return {name for name, enabled in {
        'gsc': cfg.get('gsc_property') and 'google' in keys,
        'yandex': cfg.get('webmaster_host_id') and 'yandex' in keys,
        'metrika': cfg.get('metrika_counter') and 'yandex' in keys,
        'access_log': cfg.get('access_log'), 'funnel': cfg.get('funnel_digest'),
        'site-digest': cfg.get('site_digest'), 'crawl': cfg.get('site_files'),
        **{'wordstat_'+str(i): f for i, f in enumerate(cfg.get('wordstat_files', []))}
    }.items() if enabled}
