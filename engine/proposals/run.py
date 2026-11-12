"""Deterministic drafts from measured signals. Scores are priorities, not forecasts."""
import datetime as dt
import hashlib
import json
import re
from engine.config import page_path, write_json, read_json
from engine.signals.run import cohort, window_rows
from engine.store.db import connect, upsert

KINDS = {
    'metadata': ('title/description', 'low', 'Уточнить заголовок и описание страницы'),
    'rewrite': ('переписывание', 'medium', 'Уточнить текст и прямой ответ на странице'),
    'links': ('внутренние ссылки', 'low', 'Добавить или исправить внутренние ссылки'),
    'article': ('новая статья', 'medium', 'Подготовить новую статью по теме страницы'),
    'merge': ('объединение', 'high', 'Проверить объединение страниц с одним запросом'),
    'technical': ('техническая правка', 'medium', 'Исправить техническую ошибку страницы'),
    'canonical': ('техническая правка', 'high', 'Проверить canonical или запрет индексации'),
    'price': ('переписывание', 'high', 'Проверить изменение цены'),
    'removal': ('техническая правка', 'high', 'Проверить снятие страницы'),
}
TERMINAL = {'completed', 'closed', 'cancelled', 'canceled', 'rejected', 'draft', 'planned', 'finished', 'inactive'}


def active_pages(db, date):
    """Reserve both treatment and control. Unknown/unassessed statuses stay reserved.

    record accepts structured JSON with page/path/pages/treatment/control keys or
    legacy prose with URLs and paths. change_log supplies additional exact pages.
    """
    result = set()
    for row in db.execute('SELECT * FROM experiment WHERE date<=?', (date,)):
        if (row['status'] or 'unknown').lower() in TERMINAL:
            continue
        text = row['record'] or ''
        for value in re.findall(r'https?://[^\s<>`"\x27,;]+|(?<![\w/])/[^\s<>`"\x27,;]+', text):
            result.add(page_path(value.rstrip(').:—')))
        try:
            record = json.loads(text)
        except (ValueError, TypeError):
            record = {}
        def paths(value):
            if isinstance(value, str):
                result.add(page_path(value))
            elif isinstance(value, list):
                for v in value:
                    paths(v)
            elif isinstance(value, dict):
                for key in ('page', 'path', 'pages', 'treatment', 'control', 'controls', 'treatment_pages', 'control_pages'):
                    if key in value:
                        paths(value[key])
        paths(record)
        result.update(page_path(r[0]) for r in db.execute('SELECT page FROM change_log WHERE experiment_id=? AND date<=?', (row['id'], date)) if r[0])
    return result


def options(rule, numbers):
    if rule == 'tech_issues':
        issues = set(numbers.get('issues', []))
        kinds = []
        if issues & {'missing_title', 'missing_description', 'duplicate_title', 'duplicate_description'}:
            kinds.append('metadata')
        if issues & {'orphan', 'broken_internal_links'}:
            kinds.append('links')
        if 'thin_lt300' in issues:
            kinds.append('rewrite')
        if issues & {'missing_canonical', 'nonself_canonical', 'noindex'}:
            kinds.append('canonical')
        if issues - {'missing_title', 'missing_description', 'duplicate_title', 'duplicate_description', 'orphan', 'broken_internal_links', 'thin_lt300', 'missing_canonical', 'nonself_canonical', 'noindex'}:
            kinds.append('technical')
        return kinds
    return {'low_ctr': ['metadata'], 'striking_distance': ['rewrite'],
            'no_impressions': ['links'], 'two_pages_one_query': ['merge'],
            'cluster_traction': ['article'], 'not_in_yandex_index': ['technical'],
            'robot_404': ['technical'], 'ai_robot_demand': ['links'],
            'price_change': ['price'], 'remove_page': ['removal']}.get(rule, [])


def propose(cfg, date):
    db = connect(cfg)
    pages = {r['path']: dict(r) for r in db.execute('SELECT * FROM page WHERE current_crawl<=?', (date,))}
    blocked = active_pages(db, date)
    metrics, window = window_rows(db, 'page_day', date)
    metrics = {r['page']: r for r in metrics if not r['dimension']}
    drafts = []
    for signal in db.execute('SELECT * FROM signal WHERE date=? ORDER BY rule,subject', (date,)):
        nums = json.loads(signal['numbers'])
        measured_end = (nums.get('window') or [date, date])[1]
        end = min(date, measured_end)
        def eligible(path):
            return path in pages and path not in blocked and cohort(pages[path]['published'], end) == '28+'
        rule = signal['rule']
        targets = nums.get('pages', []) if rule == 'two_pages_one_query' else [signal['subject']]
        if rule == 'cluster_traction':
            targets = [p for p, row in pages.items() if (row['cluster'] or row['section']) == signal['subject'] and eligible(p)]
            targets = sorted(targets, key=lambda p: (-metrics.get(p, {}).get('impressions', 0), p))[:1]
        if not isinstance(targets, list) or not targets or any(not eligible(p) for p in targets):
            continue
        for kind in options(rule, nums):
            label, risk, action = KINDS[kind]
            reach = max(1, nums.get('impressions', nums.get('requests', sum(metrics.get(p, {}).get('impressions', 0) for p in targets))))
            potential = .25 if kind == 'metadata' else .2 if kind == 'links' else .15
            confidence = .8 if rule == 'tech_issues' else .6 if rule in ('low_ctr', 'striking_distance') else .35
            before = nums.get('window') or window
            metric = 'CTR и клики GSC' if kind == 'metadata' else 'Клики GSC'
            rollback = 'Восстановить сохранённую до правки версию только затронутых файлов и проверить страницу.'
            hypothesis = action+' — это может увеличить переходы из поиска; эффект пока неизвестен.'
            review = (dt.date.fromisoformat(date)+dt.timedelta(days=42)).isoformat()
            experiment = {'hypothesis': hypothesis, 'metric': metric, 'before_window': before,
                          'success_threshold': 'Рост '+('CTR' if kind == 'metadata' else 'кликов')+' на 20% относительно контроля при сопоставимых показах; при малой базе вывод отложить.',
                          'rollback': rollback, 'review_date': review,
                          'caveat': 'Проверять после 4–6 недель с подтверждённой переиндексации. Дата плановая. Отсутствие строк не равно нулю.'}
            payload = {'kind': label, 'risk': risk, 'action': action, 'pages': targets,
                       'reach': reach, 'potential': potential, 'confidence': confidence,
                       'score': round(reach*potential*confidence, 4), 'control': None,
                       'reason': {'rule': rule, 'numbers': nums, 'source_rows': json.loads(signal['source_rows'])},
                       'experiment': experiment}
            labels = ['Гипотеза', 'Метрика', 'Окно до', 'Порог успеха', 'Откат', 'Дата проверки', 'Ограничения']
            payload['experiment_record'] = '\n'.join(f'{label}: {value}' for label, value in zip(labels, experiment.values()))
            identity = json.dumps([date, rule, signal['subject'], kind], ensure_ascii=False)
            drafts.append(dict(id=hashlib.sha256(identity.encode()).hexdigest()[:24], signal_date=date,
                               rule=rule, page=targets[0], payload=payload))
    for row in drafts:
        target = pages[row['page']]
        candidates = [p for p, value in pages.items() if p not in blocked | set(row['payload']['pages']) and value['section'] == target['section']
                      and cohort(value['published'], (window or [date, date])[1]) == '28+' and p in metrics and row['page'] in metrics]
        candidates.sort(key=lambda p: (pages[p]['cluster'] != target['cluster'],
                        abs(metrics[p]['impressions']-metrics[row['page']]['impressions']),
                        abs((metrics[p]['position'] or 0)-(metrics[row['page']]['position'] or 0)), p))
        row['payload']['control'] = candidates[0] if candidates else None
        row['payload']['control_note'] = 'Кандидат того же раздела с близкими показами и позицией; оставить без правки.' if candidates else 'Сопоставимый свободный контроль не найден; причинный вывод отложить.'
    with db:
        db.execute('DELETE FROM proposal WHERE signal_date=?', (date,))
        for row in drafts:
            upsert(db, 'proposal', **dict(row, payload=json.dumps(row['payload'], ensure_ascii=False)))
    db.close()
    write_json(cfg['data']/'proposals'/(date+'.json'), drafts)
    return {'date': date, 'proposals': len(drafts), 'high': sum(r['payload']['risk'] == 'high' for r in drafts)}


def card(cfg, date):
    day = dt.date.fromisoformat(date)
    week = (day-dt.timedelta(days=day.weekday())).isoformat()
    path = cfg['data']/'cards'/(week+'.json')
    db = connect(cfg)
    blocked = active_pages(db, date)
    candidates = []
    for row in db.execute('SELECT * FROM proposal WHERE signal_date=?', (date,)):
        p = json.loads(row['payload'])
        if not blocked.intersection(p['pages']):
            candidates.append(dict(p, id=row['id'], page=row['page']))
    db.close()
    candidates.sort(key=lambda p: (-p['score'], p['id']))
    existing = read_json(path) if path.exists() else {}
    chosen = existing.get('proposal')
    if chosen and blocked.intersection(chosen['pages'] + ([chosen['control']] if chosen['control'] else [])):
        existing['withdrawn'] = True
    if not chosen:
        chosen = next((p for p in candidates if p['risk'] in ('low', 'medium')), None)
    high = [p for p in candidates if p['risk'] == 'high']
    result = {'week': week, 'asof': date, 'proposal': chosen, 'needs_consent': high,
              'withdrawn': existing.get('withdrawn', False)}
    reasons = {'low_ctr': 'Страницу показывают в поиске, но переходов мало', 'striking_distance': 'Страница близка к первой десятке', 'tech_issues': 'При обходе найдены ошибки', 'no_impressions': 'В доступном окне нет возвращённых показов', 'cluster_traction': 'В теме уже есть поисковый спрос', 'ai_robot_demand': 'Страницу запрашивают роботы помощников', 'not_in_yandex_index': 'Есть явный отрицательный статус индексации', 'robot_404': 'Роботы встречают ошибку 404'}
    text = f'Карточка недели {week}\n'
    if chosen and not result['withdrawn']:
        text += (f"Что: {chosen['action']}: {chosen['page']}\n"
                 f"Почему: {reasons.get(chosen['reason']['rule'], 'Есть сигнал для проверки')}; очки {chosen['score']} = охват × потенциал × уверенность.\n"
                 f"Что будет: {chosen['experiment']['hypothesis']}\n"
                 f"Как откатить: {chosen['experiment']['rollback']}\n"
                 f"Контроль: {chosen['control'] or 'не найден'}. {chosen['control_note']}\n"
                 f"Проверить: {chosen['experiment']['review_date']}. До запуска сохранить исходники.\n")
    else:
        text += 'Выбор отозван: страница или её контроль заняты опытом.\n' if result['withdrawn'] else 'Подходящих предложений пока нет.\n'
    if high:
        text += 'Нужно согласие (в недельный выбор не входит):\n'+''.join(f"{p['action']}: {', '.join(p['pages'])}\n" for p in high)
    text += 'Это черновик. Изменения сайта и запуск опыта не выполнялись.\n'
    result['text'] = text
    write_json(path, result)
    path.with_suffix('.txt').write_text(text, encoding='utf-8')
    return result
