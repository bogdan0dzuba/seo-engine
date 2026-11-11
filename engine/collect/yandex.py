import datetime as dt
from urllib.parse import quote, urlencode
from engine.collect.http import keychain, request
from engine.config import write_json


def collect(cfg, folder, start, end):
    token = keychain(cfg['keychain']['yandex'])
    call = lambda path, body=None: request(path, token, body)
    root = 'https://api.webmaster.yandex.net/v4'
    user = call(root+'/user')
    write_json(folder/'wm_user.json', user)
    base = root+'/user/'+str(user['user_id'])+'/hosts/'+quote(cfg['webmaster_host_id'], safe='')
    history_start = min(start, (dt.date.fromisoformat(end)-dt.timedelta(days=60)).isoformat())
    dates = urlencode({'date_from': history_start+'T00:00:00Z', 'date_to': end+'T23:59:59Z'})
    endpoints = {'summary': '/summary', 'in_search_history': '/search-urls/in-search/history?'+dates,
                 'indexing_history': '/indexing/history?'+dates, 'sitemaps': '/sitemaps', 'diagnostics': '/diagnostics',
                 'queries_history': '/search-queries/all/history?'+urlencode({'date_from': history_start, 'date_to': end})+'&query_indicator=TOTAL_SHOWS&query_indicator=TOTAL_CLICKS'}
    for name, path in endpoints.items():
        write_json(folder/('wm_'+name+'.json'), call(base+path))
    for name, path in [('in_search_samples', '/search-urls/in-search/samples'), ('indexing_samples', '/indexing/samples')]:
        rows = []
        offset = 0
        while True:
            p = call(base+path+'?'+urlencode({'limit': 100, 'offset': offset}))
            chunk = p.get('samples', [])
            rows.extend(chunk)
            if len(chunk) < 100 or len(rows) >= p.get('count', len(rows)):
                break
            offset += 100
        write_json(folder/('wm_'+name+'.json'), {'samples': rows, 'count': p.get('count', len(rows)), 'complete': len(rows) >= p.get('count', len(rows))})
    for ind in ('TOTAL_SHOWS', 'TOTAL_CLICKS'):
        qs = urlencode({'date_from': start, 'date_to': end, 'order_by': ind, 'query_indicator': ['TOTAL_SHOWS', 'TOTAL_CLICKS', 'AVG_SHOW_POSITION'], 'device_type_indicator': 'ALL', 'limit': 500}, doseq=True)
        write_json(folder/('wm_popular_'+ind.lower()+'.json'), call(base+'/search-queries/popular?'+qs))
    for kind in ('QUERY', 'URL'):
        pages = []
        offset = 0
        while True:
            p = call(base+'/query-analytics/list', {'offset': offset, 'limit': 500, 'device_type_indicator': 'ALL', 'text_indicator': kind, 'region_ids': [], 'filters': {}, 'date_from': start, 'date_to': end})
            pages.append({'offset': offset, 'status': 200, 'payload': p})
            if len(p.get('text_indicator_to_statistics', [])) < 500:
                break
            offset += 500
        write_json(folder/('wm_analytics_'+kind.lower()+'.json'), pages)
    reports = [('organic_by_landing', 'ym:s:startURL,ym:s:searchEngineName', "ym:s:lastTrafficSource=='organic'"),
               ('sources_by_landing', 'ym:s:startURL,ym:s:lastTrafficSource', None),
               ('daily', 'ym:s:date,ym:s:lastTrafficSource', None),
               ('referrers', 'ym:s:date,ym:s:startURL,ym:s:referer', None)]
    for name, dims, flt in reports:
        offset, rows = 1, []
        while True:
            qs = {'ids': cfg['metrika_counter'], 'date1': start, 'date2': end, 'dimensions': dims,
                  'metrics': 'ym:s:visits,ym:s:users', 'accuracy': 'full', 'limit': 10000, 'offset': offset}
            if flt:
                qs['filters'] = flt
            p = call('https://api-metrika.yandex.net/stat/v1/data?'+urlencode(qs))
            chunk = p.get('data', [])
            rows.extend(chunk)
            if len(chunk) < 10000:
                break
            offset += 10000
        p['data'] = rows
        write_json(folder/('ym_'+name+'.json'), p)
