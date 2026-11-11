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
    result = {}
    for family, url in read_json(ROOT/'playbooks/ip_sources.json').items():
        result[family] = {'source': url, 'payload': request(url)}
    write_json(ROOT/'playbooks/ip_ranges'/('ranges-'+date+'.json'), {'date': date, 'families': result})


def ranges_for(date):
    paths = sorted(p for p in (ROOT/'playbooks/ip_ranges').glob('ranges-*.json') if p.stem[7:] <= date)
    if not paths:
        return {}
    payload = read_json(paths[-1])
    # Stale IP snapshots cannot establish current identity.
    if (dt.date.fromisoformat(date)-dt.date.fromisoformat(payload['date'])).days > 30:
        return {}
    result = {}
    for family, entry in payload.get('families', {}).items():
        result[family] = [ipaddress.ip_network(v) for row in entry['payload'].get('prefixes', [])
                          for k, v in row.items() if k in ('ipv4Prefix', 'ipv6Prefix')]
    return result


def header(headers, name):
    v = next((v for k, v in headers.items() if k.lower() == name.lower()), [])
    return ' '.join(v) if isinstance(v, list) else str(v)


def assistant(referrer, domains):
    host = (urlsplit(referrer).hostname or '').lower()
    return next((d for d in domains if host == d or host.endswith('.'+d)), '')


def parse(cfg, output):
    families = {name: cls for cls, names in read_json(ROOT/'playbooks/ai_bots.yaml').items() for name in names}
    ranges = {}
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
                ranges.setdefault(date, ranges_for(date))
                verified = 'unverified'
                try:
                    addr = ipaddress.ip_address(ip)
                    if any(addr in net for net in ranges[date].get(family, [])):
                        verified = 'verified'
                except ValueError:
                    pass
                if re.search(r'(?:^|/)(?:\.env(?:\.|/|$)|\.git|wp-admin|wp-login|phpmyadmin)|\.(?:sql|bak)$', p, re.I) and cls != 'unknown':
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
