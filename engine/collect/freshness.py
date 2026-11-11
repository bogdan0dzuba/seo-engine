"""Only dated nonzero observations count; request metadata never does."""
import calendar
import datetime as dt


def iso(value):
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def real_dates(value):
    dates = []
    if isinstance(value, dict):
        if iso(value.get('date')) and isinstance(value.get('value'), (int, float)) and value['value'] != 0:
            dates.append(iso(value['date']))
        for v in value.values():
            dates += real_dates(v)
    elif isinstance(value, list):
        if len(value) == 2 and iso(value[0]) and isinstance(value[1], (float, int)) and value[1] != 0:
            dates.append(iso(value[0]))
        else:
            for v in value:
                dates += real_dates(v)
    return dates


def yandex_evidence(payloads):
    evidence = {}
    recon = payloads.get('wm_index_check', {})
    for key in ('in_search_history', 'indexing_history'):
        dates = real_dates(payloads.get('wm_'+key, recon.get(key, {})))
        evidence[key] = max(dates, default=None)
    maps = payloads.get('wm_sitemaps', recon.get('sitemaps', []))
    if isinstance(maps, dict):
        maps = maps.get('sitemaps', [])
    evidence['sitemap_last_access'] = max((iso(x.get('last_access_date')) for x in maps if iso(x.get('last_access_date'))), default=None)
    return evidence


def check(newest, today):
    age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(newest)).days if newest else None
    return {'newest_real': newest, 'age_days': age, 'status': 'fresh' if age is not None and 0 <= age <= 7 else 'STALE'}


def assert_fresh(newest, today):
    result = check(newest, today)
    if result['status'] != 'fresh':
        raise ValueError('STALE: newest real observation '+str(newest))
    return result


def month_end(month):
    try:
        y, m = map(int, month.split('-'))
        return dt.date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    except (ValueError, AttributeError):
        return None
