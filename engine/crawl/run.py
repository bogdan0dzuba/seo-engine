"""Read HTML and sitemap trees from disk. Never fetch or modify the website."""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from engine.config import disk_file, page_path, section, write_json


def walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.visible, self.title, self.h1, self.links, self.canonicals = [], [], [], [], [], []
        self.meta, self.ld, self.h1_buffer, self.ld_buffer = {}, [], None, None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, 'hidden' in a or a.get('aria-hidden') == 'true'))
        if tag == 'h1':
            self.h1_buffer = []
        if tag == 'meta':
            self.meta[a.get('name', a.get('property', '')).lower()] = a.get('content', '')
        if tag == 'link' and 'canonical' in a.get('rel', '').split():
            self.canonicals.append(a.get('href', ''))
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'script' and a.get('type', '').lower() == 'application/ld+json':
            self.ld_buffer = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'h1' and self.h1_buffer is not None:
            self.h1.append(' '.join(''.join(self.h1_buffer).split()))
            self.h1_buffer = None
        if tag == 'script' and self.ld_buffer is not None:
            self.ld.append(''.join(self.ld_buffer))
            self.ld_buffer = None
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        tags = [t for t, _ in self.stack]
        if 'title' in tags:
            self.title.append(data)
        if self.h1_buffer is not None:
            self.h1_buffer.append(data)
        if self.ld_buffer is not None:
            self.ld_buffer.append(data)
        if not any(t in ('head', 'script', 'style', 'template', 'noscript') or hidden for t, hidden in self.stack):
            self.visible.append(data)


def sitemap_urls(cfg):
    root = Path(cfg['site_files']).expanduser().resolve()
    pending, seen, urls = [root/cfg.get('sitemap', 'sitemap-index.xml')], set(), set()
    while pending:
        f = pending.pop().resolve()
        if not f.is_relative_to(root):
            raise ValueError('Sitemap outside site root')
        if f in seen:
            continue
        seen.add(f)
        tree = ET.fromstring(f.read_bytes())
        locs = [el.text for el in tree.iter() if el.tag.split('}')[-1] == 'loc' and el.text]
        for loc in locs:
            if urlsplit(loc).netloc != urlsplit(cfg['domain']).netloc:
                raise ValueError('Sitemap URL outside configured domain')
            if tree.tag.split('}')[-1] == 'sitemapindex':
                pending.append(disk_file(root, loc))
            else:
                urls.add(loc)
    return sorted(urls)


def crawl(cfg, date):
    if not cfg.get('site_files'):
        return {'pages': 0, 'issues': 0, 'status': 'disabled'}
    rows = []
    host = urlsplit(cfg['domain']).netloc
    for url in sitemap_urls(cfg):
        path = page_path(url)
        f = disk_file(cfg['site_files'], path)
        row = {'page': path, 'section': section(cfg, path), 'file': str(f), 'issues': []}
        if not f.is_file():
            row.update(title='', description='', h1=[], canonical=[], jsonld_types=[], words=0, published=None, links=[], sha256='')
            row['issues'].append('missing_file')
        else:
            raw = f.read_bytes()
            p = Page(raw.decode(errors='replace'))
            nodes, errors = [], 0
            for ld in p.ld:
                try:
                    nodes.extend(walk(json.loads(ld)))
                except (ValueError, TypeError):
                    errors += 1
            types = set()
            for n in nodes:
                t = n.get('@type', [])
                types.update(t if isinstance(t, list) else [t])
            dates = sorted(str(n['datePublished'])[:10] for n in nodes if n.get('datePublished'))
            links = sorted({page_path(u) for href in p.links if urlsplit(u := urljoin(url, href)).netloc == host})
            row.update(title=' '.join(''.join(p.title).split()), description=p.meta.get('description', ''),
                       h1=p.h1, canonical=p.canonicals, jsonld_types=sorted(types),
                       words=len(re.findall(r'\b[\w]+(?:[-’][\w]+)*\b', ' '.join(p.visible))),
                       published=dates[0] if dates else p.meta.get('article:published_time', '')[:10] or None,
                       links=links, sha256=hashlib.sha256(raw).hexdigest())
            checks = {'missing_title': not row['title'], 'missing_description': not row['description'],
                      'h1_not_one': len(p.h1) != 1, 'missing_canonical': not p.canonicals,
                      'nonself_canonical': any(urlsplit(c).netloc != host or page_path(c) != path for c in p.canonicals),
                      'invalid_jsonld': errors > 0, 'noindex': 'noindex' in p.meta.get('robots', '').lower(),
                      'thin_lt300': row['words'] < 300}
            row['issues'] = [k for k, v in checks.items() if v]
        rows.append(row)
    known = {r['page'] for r in rows}
    inbound = {p: set() for p in known}
    for row in rows:
        for target in row['links']:
            if target in inbound and target != row['page']:
                inbound[target].add(row['page'])
    for row in rows:
        row['incoming'] = len(inbound[row['page']])
        if not row['incoming'] and row['page'] != '/':
            row['issues'].append('orphan')
        row['broken_links'] = [p for p in row['links'] if not disk_file(cfg['site_files'], p).is_file()]
        if row['broken_links']:
            row['issues'].append('broken_internal_links')
    for field in ('title', 'description'):
        groups = {}
        for r in rows:
            if r[field]:
                groups.setdefault(r[field], []).append(r)
        for group in groups.values():
            if len(group) > 1:
                for row in group:
                    row['issues'].append('duplicate_'+field)
    write_json(cfg['data']/'raw'/date/'crawl.json', rows)
    return {'pages': len(rows), 'issues': sum(len(r['issues']) for r in rows)}
