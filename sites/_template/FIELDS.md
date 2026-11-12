# Поля site.yaml

Формат — строгий JSON, совместимый с YAML. Комментарии внутри файла не
поддерживаются; пояснения хранятся здесь. Минимум — domain, sections, data_dir.
`init-site` создаёт только эти три поля и не перезаписывает существующий файл.
Имя сайта: строчные латинские буквы, цифры, дефис, подчёркивание.

| Поле | Значение и поведение |
|---|---|
| domain | Обязательный HTTP(S) origin без пути, например https://example.org |
| sections | Обязательный список префиксов путей: ["/", "/guides/"] |
| data_dir | Обязательный каталог raw, SQLite, отчётов, предложений и карточек. По умолчанию init-site: абсолютный путь checkout/.data/имя |
| article_section | Префикс статей для возрастных когорт; по умолчанию /blog/ |
| site_files | Локальная копия HTML. Без неё crawl отключён; сайт по HTTP не обходится |
| sitemap | Путь sitemap относительно site_files; по умолчанию sitemap-index.xml; поддерживается индекс sitemap |
| snapshot_dir | Каталог офлайн-снимков с подпапками YYYY-MM-DD/*.json; существующие raw не заменяются |
| backfill_start | Первая дата импорта с --backfill; без поля — все доступные до --date |
| gsc_property | Точное имя ресурса GSC: URL-prefix или sc-domain:example.org |
| webmaster_host_id | Идентификатор сайта Вебмастера, например https:example.org:443 |
| metrika_counter | Номер счётчика Метрики; независим от webmaster_host_id |
| keychain | Объект google/yandex, каждый со своими account и service. Имена выбирает владелец. Google: service-account JSON или его hex; Yandex: OAuth token. Без записи источника API отключён |
| access_log | Путь JSON access log; без поля источник отключён |
| client_address_header | Доверенный заголовок адреса клиента; по умолчанию X-Forwarded-For. Принимать его только от своего proxy |
| internal_user_agents | Регулярные выражения внутренних проверок; по умолчанию Headless, python-urllib, healthcheck |
| internal_query_markers | Подстроки query для исключения внутренних проверок, по умолчанию [] |
| ai_referrers | Список доменов помощников; по умолчанию [] |
| funnel_digest / site_digest | Путь локального JSON с подстановкой {date}; отсутствие поля отключает источник |
| wordstat_files | Список путей сохранённых JSON Wordstat; по умолчанию []; API Wordstat движок не вызывает |
| index_evidence / index_evidence_date | Необязательный исторический импорт подтверждений индексации с явной датой; только backfill |
| effects_file | Необязательный JSON-массив записей experiment (id, date, status, record) |
| signal_thresholds | Необязательные пороги: low_ctr=.02, striking_distance_min_impressions=20, low_ctr_min_impressions=50, two_pages_min_impressions=10, ai_robot_min_hits=3, robot_404_min_hits=3, new_bot_min_hits=10 |

Пути файлов разрешаются относительно рабочего каталога; `~` разворачивается.
Для переносимости указывайте абсолютные пути. `SEO_ENGINE_DATA` при наличии
переопределяет data_dir точным каталогом одного сайта, а не общим родителем.
Отсутствующий необязательный источник не считается ошибкой. Настроенный источник
без свежих данных даёт STALE. Явно заданный, но неверный Keychain-item даёт ошибку
сбора; обходного поиска ключей нет. Офлайн-снимки анализируются и без ключей API.
