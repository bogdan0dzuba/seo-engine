# REPORT · Контур · 2026-11-12

PASS 1: engine/collect,crawl,store,signals,report; CLI, playbooks, tests, docs; stdlib без pip.
PASS 2: site.yaml: домен/источники/Keychain names; ai_referrers без ya.ru и openai.com, остальные сохранены.
PASS 3: endpoint isolation + безопасные manifests; GSC, queries и все 4 отчёта Метрики live, Яндекс частично.
PASS 4: freshness в source_status; stale/all-zero не проходят; signals сохраняет результат и возвращает 2.
PASS 5: crawl с диска: 284 URL, 239 статей; metadata/JSON-LD/даты/граф/хеши.
PASS 6: идемпотентные SQLite/raw/backfill 05–11.11; 3 опыта из sites/lida/import/effects.json.
PASS 7: 13 правил с score/numbers/source_rows; пороги 20/50/10 показов, AI/404 >=3; probe_noise отдельно.
PASS 8: .data/lida/reports/2026-11-09.{html,txt,json}: top 10 каждого правила + totals, Δ окон и robots по семействам.
PASS 9: CLI/nightly/plist-шаблон; launchd не установлен.
PASS проверки: python3 -m unittest — 29 тестов; добавлены регрессии всех исправлений; tiny другой домен.
PASS повторная загрузка: одинаковые counts и rowids page_day/query_day/bot_day/source_status; integrity_check=ok.
PASS границы: нет Lida в engine/*.py; git commit/push не запускались; изменения только в рабочем дереве.
Данные прогона: .data/lida/; raw=135 JSON; локальные источники + backfill + новые API ответы.
Строки: bot_day=1976; crawl_page=284; experiment=3; index_day=84; log_request=7403; metric_day=1090; page=284; page_day=1497; query_day=3786; source_snapshot=135; source_status=38.
Пустые таблицы: ai_panel, ai_visibility, change_log, decision, effect, proposal, stat_registry.
Сигналы (всего 72, было 527): striking_distance=20; low_ctr=4; no_impressions=2; two_pages_one_query=1; cluster_traction=11; not_in_yandex_index=0; ai_robot_demand=12; robot_404=4; tech_issues=4; stale_source=4; agent_trust_page=6; new_bot_family=3; probe_noise=1.
Когорты на конец GSC окна 2026-10-13…2026-11-09; отсутствие строки ≠ измеренный ноль.

| Возраст | Статей | С показами | Без возвращённых показов | Доля (только 28+) |
|---|---:|---:|---:|---:|
| 28+ | 63 | 61 | 2 | 3.17% |
| 14–27 | 16 | 9 | 7 | — |
| 7–13 | 14 | 8 | 6 | — |
| 0–6 | 110 | 21 | 89 | — |
| after window | 36 | 0 | 36 | — |
| unknown | 0 | 0 | 0 | — |

Последние реальные даты: access_log=2026-11-11 fresh; crawl=2026-11-12 fresh; funnel=2026-11-10 fresh; gsc=2026-11-09 fresh; metrika=2026-11-10 fresh; site-digest=2026-11-10 fresh; wordstat_0=None STALE; wordstat_1=2026-09-30 STALE; wordstat_2=None STALE; yandex=2026-10-09 STALE.
Yandex: in-search=2026-10-07; indexing=2026-10-06; sitemap last_access=2026-10-09.
Δ последних окон 2026-10-12…11-08 → 2026-10-13…11-09: клики 137→137; показы 3531→3692 (+161).
По разделам: / +2, /blog/ +155, /dlya/ +1, /resheniya/ +1, other +2 показов; клики без изменения.
Окна скользящие и пересекаются; top 10 изменившихся страниц в HTML/JSON, ранжирование |Δclicks|, затем |Δimpressions|.
Top 10 striking-distance страниц: сортировка impressions×(21−position), окно GSC выше.

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

Живой сбор: GSC PASS; Webmaster summary/history/samples/sitemaps/diagnostics/popular/query analytics и 4 Метрики получены.
PARTIAL /indexing/samples: CollectionError; сохранён в raw/2026-11-12/{collection,yandex_manifest}.json; collect exit=1 в конце.
Wordstat/funnel/digest — существующие файлы; Wordstat 0/2 содержат HTTP 403. Свежесть Яндекса/Wordstat остаётся STALE.
IP: ближайший снимок ±30 дней; исходные 6 семейств сохранены, Bing/3 Claude добавлены из официальных JSON ответов.
Ограничение IP update: CLI встретил HTTP 403 и не повторял endpoint; официальные Bing/Claude ответы прочитаны до него.
Роботы 2026-11-06…12: verified=386, unverified=48, unavailable=1785, spoofed=72; четыре счётчика каждого семейства в HTML/JSON.
Index evidence: свои raw/wm_in_search_samples.json; recon импорт только 2026-11-10 (15 first из 69, complete=false).
Ограничения: нет отрицательных index samples; нет причинных выводов, API моделей, отправок или изменений сайта.
