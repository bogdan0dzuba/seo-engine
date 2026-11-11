import datetime as dt
from urllib.parse import quote, urlencode
from engine.collect.http import keychain, request
from engine.config import write_json
from engine.collect.endpoint import Endpoints


def collect(cfg, folder, start, end):
    endpoints_run = Endpoints(folder, 'yandex')
    token = endpoints_run.attempt('yandex/credentials', lambda: keychain(cfg['keychain']['yandex']))
    if token is None:
        return endpoints_run.errors
    call = lambda path, body=None: request(path, token, body)
    root = 'https://api.webmaster.yandex.net/v4'
    def fetch_user():
        user = call(root+'/user')
        user['user_id']  # Check the dependency inside the endpoint boundary.
        return user
    user = endpoints_run.save('wm_user', root+'/user', fetch_user)
    if user is not None:
        base = root+'/user/'+str(user['user_id'])+'/hosts/'+quote(cfg['webmaster_host_id'], safe='')
        history_start = min(start, (dt.date.fromisoformat(end)-dt.timedelta(days=60)).isoformat())
        dates = urlencode({'date_from': history_start+'T00:00:00Z', 'date_to': end+'T23:59:59Z'})
        endpoints = {'summary': '/summary', 'in_search_history': '/search-urls/in-search/history?'+dates,
                     'indexing_history': '/indexing/history?'+dates, 'sitemaps': '/sitemaps', 'diagnostics': '/diagnostics',
                     'queries_history': '/search-queries/all/history?'+urlencode({'date_from': history_start, 'date_to': end})+'&query_indicator=TOTAL_SHOWS&query_indicator=TOTAL_CLICKS'}
        for name, path in endpoints.items():
            endpoints_run.save('wm_'+name, path.split('?')[0], lambda: call(base+path))
        for name, path in [('in_search_samples', '/search-urls/in-search/samples'), ('indexing_samples', '/indexing/samples')]:
            def samples():
                rows, offset, count = [], 0, None
                try:
                    while True:
                        p = call(base+path+'?'+urlencode({'limit': 100, 'offset': offset}))
                        chunk = p.get('samples', [])
                        rows.extend(chunk)
                        count = p.get('count', len(rows))
                        if len(chunk) < 100 or len(rows) >= count:
                            return {'samples': rows, 'count': count, 'complete': len(rows) >= count}
                        offset += 100
                finally:
                    write_json(folder/('wm_'+name+'.json'), {'samples': rows, 'count': count, 'complete': False})
            endpoints_run.save('wm_'+name, path, samples)
        for ind in ('TOTAL_SHOWS', 'TOTAL_CLICKS'):
            qs = urlencode({'date_from': start, 'date_to': end, 'order_by': ind, 'query_indicator': ['TOTAL_SHOWS', 'TOTAL_CLICKS', 'AVG_SHOW_POSITION'], 'device_type_indicator': 'ALL', 'limit': 500}, doseq=True)
            endpoints_run.save('wm_popular_'+ind.lower(), '/search-queries/popular/'+ind, lambda: call(base+'/search-queries/popular?'+qs))
        for kind in ('QUERY', 'URL'):
            def analytics():
                pages, offset = [], 0
                try:
                    while True:
                        p = call(base+'/query-analytics/list', {'offset': offset, 'limit': 500, 'device_type_indicator': 'ALL', 'text_indicator': kind, 'region_ids': [], 'filters': {}, 'date_from': start, 'date_to': end})
                        pages.append({'offset': offset, 'status': 200, 'payload': p})
                        if len(p.get('text_indicator_to_statistics', [])) < 500:
                            return pages
                        offset += 500
                finally:
                    write_json(folder/('wm_analytics_'+kind.lower()+'.json'), pages)
            endpoints_run.save('wm_analytics_'+kind.lower(), '/query-analytics/list/'+kind, analytics)
    reports = [('organic_by_landing', 'ym:s:startURL,ym:s:searchEngineName', "ym:s:lastTrafficSource=='organic'"),
               ('sources_by_landing', 'ym:s:startURL,ym:s:lastTrafficSource', None),
               ('daily', 'ym:s:date,ym:s:lastTrafficSource', None),
               ('referrers', 'ym:s:date,ym:s:startURL,ym:s:referer', None)]
    # Metrika uses the shared token but has no Webmaster dependency.
    for name, dims, flt in reports:
        def metrika():
            offset, rows, payload = 1, [], None
            try:
                while True:
                    qs = {'ids': cfg['metrika_counter'], 'date1': start, 'date2': end, 'dimensions': dims,
                          'metrics': 'ym:s:visits,ym:s:users', 'accuracy': 'full', 'limit': 10000, 'offset': offset}
                    if flt:
                        qs['filters'] = flt
                    payload = call('https://api-metrika.yandex.net/stat/v1/data?'+urlencode(qs))
                    chunk = payload.get('data', [])
                    rows.extend(chunk)
                    if len(chunk) < 10000:
                        payload['data'] = rows
                        return dict(payload, data=rows, _complete=True)
                    offset += 10000
            finally:
                if payload is not None:
                    payload['data'] = rows
                    payload['_complete'] = False
                    write_json(folder/('ym_'+name+'.json'), payload)
        endpoints_run.save('ym_'+name, 'metrika/'+name, metrika)
    return endpoints_run.errors
