"""Durable per-request fingerprints allow overlapping/rotated logs without double counting.
Raw IPs and request query strings are deliberately not persisted.
"""
import collections
import datetime as dt
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from engine.config import ROOT, read_json, write_json, page_path


def fetch_ranges(date):
    from engine.collect.http import request
    path = ROOT/'playbooks/ip_ranges'/('ranges-'+date+'.json')
    result = read_json(path).get('families', {}) if path.exists() else {}
    cache, errors = {}, []
    families = {n for names in read_json(ROOT/'playbooks/ai_bots.yaml').values() for n in names}
    sources = read_json(ROOT/'playbooks/ip_sources.json')
    for family in sorted(families):
        url = sources.get(family)
        if url is None:
            result[family] = {'verification': 'unavailable'}
        else:
            if url not in cache:
                try:
                    cache[url] = request(url)
                except Exception as error:
                    cache[url] = None
                    errors.append({'endpoint': url, 'error_class': type(error).__name__})
            if cache[url] is not None:
                result[family] = {'source': url, 'payload': cache[url]}
    write_json(path, {'date': date, 'families': result, 'errors': errors})
    return errors


def ranges_for(date):
    day = dt.date.fromisoformat(date)
    candidates = []
    for path in (ROOT/'playbooks/ip_ranges').glob('ranges-*.json'):
        snapshot = dt.date.fromisoformat(path.stem[7:])
        distance = abs((day-snapshot).days)
        if distance <= 30:
            candidates.append((distance, snapshot > day, path))
    if not candidates:
        return {}
    payload = read_json(min(candidates)[2])
    return {family: [ipaddress.ip_network(v) for row in entry.get('payload', {}).get('prefixes', [])
                     for k, v in row.items() if k in ('ipv4Prefix', 'ipv6Prefix')]
            for family, entry in payload.get('families', {}).items()}


def is_probe(path):
    return bool(re.search(r'(?:^|/)(?:\.env(?:[./]|$)|\.git(?:/|$)|\.aws(?:/|$)|wp-[^/]*|wordpress|phpmyadmin|xmlrpc\.php|vendor/phpunit|actuator|server-status|config\.php)|\.(?:sql|bak)$', path, re.I))



def header(headers, name):
    v = next((v for k, v in headers.items() if k.lower() == name.lower()), [])
    return ' '.join(v) if isinstance(v, list) else str(v)


def assistant(referrer, domains):
    host = (urlsplit(referrer).hostname or '').lower()
    return next((d for d in domains if host == d or host.endswith('.'+d)), '')


def parse(cfg, output):
    families = {name: cls for cls, names in read_json(ROOT/'playbooks/ai_bots.yaml').items() for name in names}
    ranges = {}
    sources = read_json(ROOT/'playbooks/ip_sources.json')
    rows, excluded, malformed = [], 0, 0
    log = Path(cfg['access_log']).expanduser()
    limit = log.stat().st_size
    consumed = 0
    occurrences = collections.Counter()
    with log.open('rb') as f:
        while consumed < limit:
            raw = f.readline()
            consumed += len(raw)
            if consumed > limit:
                break
            try:
                r = json.loads(raw)
                date = dt.datetime.fromtimestamp(r['ts'], dt.timezone.utc).date().isoformat()
                q = r.get('request', {})
                headers = q.get('headers', {})
                ua, uri = header(headers, 'User-Agent'), q.get('uri', '/')
                if any(re.search(p, ua, re.I) for p in cfg['internal_user_agents']) or any(m in uri for m in cfg.get('internal_query_markers', [])) or (ua.lower().startswith('curl/') and q.get('remote_ip') in ('127.0.0.1', '::1')):
                    excluded += 1
                    continue
                family = next((n for n in sorted(families, key=len, reverse=True) if n.lower() in ua.lower()), '')
                cls = families.get(family, 'unknown')
                if not family:
                    match = re.search(r'([\w.-]*(?:bot|spider|crawler|agent|gpt|python|curl)[\w.-]*)', ua, re.I)
                    family = 'unknown:'+match[0].lower() if match else 'other'
                p = page_path(uri)
                ip = header(headers, cfg['client_address_header']).split(',')[0].strip()
                if date not in ranges:
                    ranges[date] = ranges_for(date)
                verified = 'unverified' if family in sources else 'unavailable'
                try:
                    addr = ipaddress.ip_address(ip)
                    if any(addr in net for net in ranges[date].get(family, [])):
                        verified = 'verified'
                except ValueError:
                    pass
                if is_probe(p) and cls != 'unknown':
                    verified = 'spoofed'
                special = bool(re.search(r'^/(?:robots\.txt|llms[^/]*\.txt|sitemap[^/]*\.xml|\.well-known/|openapi|mcp|rss|feed|ai\.txt)', p) or p.endswith('.md'))
                digest = hashlib.sha256(raw).hexdigest()
                occurrences[digest] += 1
                rows.append({'id': digest+':'+str(occurrences[digest]), 'date': date, 'family': family, 'class': cls,
                             'verified': verified, 'page': p, 'status': int(r.get('status', 0)), 'special': int(special),
                             'assistant': assistant(header(headers, 'Referer'), cfg['ai_referrers']),
                             'evidence': str(log)+':sha256:'+digest})
            except (ValueError, KeyError, TypeError, OverflowError):
                malformed += 1
    write_json(output, {'requests': rows, 'excluded_internal': excluded, 'malformed': malformed, 'frozen_bytes': limit})
    return {'accepted': len(rows), 'excluded_internal': excluded, 'malformed': malformed}
