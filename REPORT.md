# REPORT · Контур · 2026-11-12

PASS 1: engine/{collect,crawl,store,signals,report}, CLI, конфиг, playbooks, tests, docs; без pip.
PASS 2: все сведения Лиды в site.yaml; default ~/seo-engine-data/lida, override SEO_ENGINE_DATA.
PASS 3: GSC, Webmaster/Метрика, логи, Wordstat/funnel/digest; live Яндекс частично (см. ниже).
PASS 4: реальные даты, 7 дней; stale/all-zero fixtures отклоняются; signals возвращает 2 при STALE.
PASS 5: crawl с диска: 284 URL, 239 статей; title/meta/H1/canonical/JSON-LD/слова/даты/граф/хеши.
PASS 6: SQLite, raw, идемпотентный backfill 05–11.11; E-0001…E-0003 импортированы.
PASS 7: 12 правил, numbers/source_rows; возраст на конец окна; index absence не выдумывается.
PASS 8: .data/lida/reports/2026-11-09.{html,txt,json}; HTML самодостаточный, только таблицы.
PASS 9: пять CLI-команд + nightly runner; plist-шаблон в docs, НЕ установлен.
PASS проверки: python3 -m unittest — 18 тестов; другой домен проходит весь CLI на tiny fixture.
PASS реальная повторная загрузка: одинаковые counts, стабильные rowid; PRAGMA integrity_check=ok.
PASS границы: grep -ri "lida" engine/ — пусто; сайт/его repo/сервер/внешние БД не изменены.
Данные прогона: .data/lida/; GSC live + снимки 05–11.11; raw=123 файла; без push и remote.
Строки таблиц: page=284; query_day=3786; page_day=1128; index_day=69; bot_day=1975.
ai_visibility=1; ai_panel=0; crawl_page=284; signal=527; experiment=3.
proposal=0; decision=0; change_log=0; effect=0; stat_registry=37.
source_snapshot=123; log_request=7403; metric_day=707.
Сигналы: striking_distance=53; low_ctr=36; no_impressions=2; two_pages_one_query=54; cluster_traction=11; not_in_yandex_index=0.
ai_robot_demand=209; robot_404=151; tech_issues=4; stale_source=4; agent_trust_page=0; new_bot_family=3.
Когорты статей на конец GSC окна ['2026-10-13', '2026-11-09']; отсутствие строки ≠ измеренный ноль.

| Возраст | Статей | С показами | Без возвращённых показов | Доля (только 28+) |
|---|---:|---:|---:|---:|
| 28+ | 63 | 61 | 2 | 3.17% |
| 14–27 | 16 | 9 | 7 | — |
| 7–13 | 14 | 8 | 6 | — |
| 0–6 | 110 | 21 | 89 | — |
| after window | 36 | 0 | 36 | — |
| unknown | 0 | 0 | 0 | — |

Последние реальные даты по источникам (не даты получения):
gsc=2026-11-09 fresh; metrika=2026-11-10 fresh; funnel=2026-11-10 fresh; site-digest=2026-11-10 fresh; access_log=2026-11-11 fresh; crawl=2026-11-12 fresh.
yandex=2026-10-09 STALE; wordstat_0=None STALE; wordstat_1=2026-09-30 STALE; wordstat_2=None STALE.
Yandex: in-search=2026-10-07; indexing=2026-10-06; sitemap last_access=2026-10-09.
Top 10 striking-distance страниц: GSC 2026-10-13…2026-11-09; сортировка impressions×(21−position).

| Страница | Показы | Клики | Позиция |
|---|---:|---:|---:|
| /blog/kak-najti-direktora-kompanii/ | 301 | 4 | 8.54 |
| /blog/klienty-dlya-logistiki/ | 202 | 12 | 9.11 |
| /blog/servisy-kholodnykh-rassylok/ | 83 | 5 | 11.71 |
| /blog/follow-up-pisma/ | 60 | 5 | 9.02 |
| /blog/ii-poisk-klientov/ | 77 | 3 | 12.03 |
| /blog/kak-najti-klientov-b2b/ | 150 | 5 | 17.95 |
| /blog/klienty-dlya-it-kompanii/ | 40 | 2 | 11.10 |
| /blog/mcp-dlya-prodazh/ | 30 | 1 | 8.30 |
| /blog/klienty-dlya-proizvodstva/ | 34 | 0 | 10.24 |
| /blog/smtp-imap-pop3/ | 28 | 0 | 9.18 |

Живой сбор: GSC PASS; Webmaster summary/history/sitemaps/diagnostics/in-search samples получены.
Yandex остановлен на /indexing/samples: CollectionError «Network or response unavailable»; повторов/обхода нет.
Поэтому indexing samples, popular, query analytics и Метрика не собраны live; доступны сохранённые query/URL/Метрика.
Wordstat/funnel/digest — чтение существующих файлов, без повторного сбора; Wordstat errors=HTTP 403.
IP: получены официальные диапазоны 6 семейств, playbooks/ip_ranges/ranges-2026-11-12.json.
Исторические запросы не подтверждаются будущим IP-снимком: verified=0, unverified=2226, spoofed=65 (bot hits).
Index rule=0: отрицательные статусы недоступны; отсутствие в samples не доказательство отсутствия в индексе.
Ограничения: до начала удержания лога дни невосстановимы; нет API моделей, отправок и установки launchd.
FAIL финальный commit: git add/commit запрещены sandbox (.git/index.lock: Operation not permitted); d35617e сохранён, финальные изменения в рабочем дереве.
