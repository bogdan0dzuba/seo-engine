# TASK for Codex (gpt-6.1-sol, standard speed): stage 2, core of the SEO engine «Контур»

## How this run is set up (12.11)
- You work in the folder you were started in: it is the new repository `seo-engine` (already `git init`, branch `main`, empty except this file, `.gitignore` and `.ref/`). Commit as you go; do not push and do not create a remote: the thread does that after acceptance.
- Your sandbox writes only inside this folder. During this run the data directory is `./.data/lida/` (git-ignored); the real path `~/seo-engine-data/lida/` goes into `site.yaml` as the default and is overridable with the environment variable `SEO_ENGINE_DATA`.
- Reference code is copied into `.ref/` (git-ignored, read-only for you): `.ref/collector/google_snapshot.py`, `.ref/collector/yandex_snapshot.py` and their tests, `.ref/EFFECTS.md`, `.ref/recon.py`, `.ref/log_http.py`, `.ref/log_reaggregate.py`, `.ref/wm_index_check.py`. Do NOT run git inside `~/Documents/lida` (it hangs).
- Reading outside the folder is allowed: `~/lida-ops/seo-data/<date>/` (daily snapshots 2026-11-05 … 2026-11-11, incl. `funnel.json`, `site-digest.json`), `~/lida/logs/site-access.log` (Caddy JSON, about 17 000 lines, real client address is in the request header `X-Forwarded-For`), `~/lida/fallback/site` (live site files), `~/lida/data/wordstat/`, `~/lida-ops/codex-results/seo-engine-recon-20261110/` (recon report and data).
- Secrets: collectors read them at run time by Keychain item name (for example account `lida`, service `LIDA_YANDEX_API_TOKEN`). If the sandbox refuses Keychain or the network, do not look for another way: build the real run from the saved snapshots and say in the report which collectors were not run live. The thread runs them after you. Never print or write a secret.
- The machine is busy with production rolls tonight: no test suites of other projects, no heavy parallel jobs, nothing outside this folder is changed.
- The site has 283 sitemap URLs now (238 blog). Lida no longer uses Supabase: nothing in the engine may depend on it.

## Goal
A reusable SEO engine that, for the site `lida`, collects all sources every night into one SQLite file and produces a list of signals and a weekly report. Principles: collection, signals, ranking and measurement are plain code (no LLM); one store per site; the engine never edits a site itself; nothing about one site lives in the core, so a second site is added with one config file. No change to the Lida site, server, DB or its repo in this stage.

## Deliverables
1. Repo layout: `engine/{collect,crawl,store,signals,report}/`, `engine/cli.py`, `playbooks/`, `sites/lida/site.yaml`, `tests/`, `docs/`, `README.md`. Python 3 stdlib plus sqlite3 only; no pip dependencies in the core (so the config parser must handle the small YAML subset you use, or use JSON-compatible YAML).
2. `sites/lida/site.yaml`: domain, sections (`/`, `/blog/`, `/dlya/`, `/resheniya/`, `/instrumenty/`, `/novoe/`), Webmaster host id, Metrika counter, GSC property, names (never values) of Keychain items, path of the access log, name of the client-address header, path of the live site files, path of the funnel digest, internal User-Agents to exclude (headless checks, deploy checks, python-urllib and similar own tooling). Nothing about Lida may be hard-coded in `engine/`.
3. `collect`: port the two collectors from `.ref/collector/` behind the config. Add to the Yandex collector: explicit date ranges, indexing and in-search history, in-search samples, sitemaps, diagnostics (see `.ref/wm_index_check.py`). Add Metrika referrer dimension with an AI-assistant domain list. Add `access_log.py` (bot and agent families, special paths, 404s, AI referrers; reuse `.ref/log_http.py` and `.ref/log_reaggregate.py`) and keep a daily aggregate, because the raw log holds only a few days. Wordstat and the funnel digest are read from their existing output files, not re-collected.
4. Freshness check per source that fails loudly when the newest REAL data point is older than 7 days. Known trap, measured on 11.11: Yandex `queries history` returns a row for every date up to today with value 0.0, so the old collector reported `stale: false` while Webmaster has been frozen since 07.10 (in-search history ends 2026-10-07, sitemap last read 2026-10-09). Rows that are all zero do not count as data; use in-search history, indexing history and sitemap last access as the evidence for Yandex.
5. `crawl`: port `.ref/recon.py` (title, description, H1, canonical, JSON-LD types, words, publication date, link graph, hashes) for every sitemap URL, from files on disk.
6. `store`: `schema.sql` with tables page, query_day, page_day, index_day, bot_day, ai_visibility, ai_panel, crawl_page, signal, proposal, decision, experiment, change_log, effect, stat_registry; idempotent loaders; raw snapshots kept under `<data>/raw/<date>/`, database `<data>/seo.sqlite`. Backfill from `~/lida-ops/seo-data/2026-11-05…2026-11-11` and import `.ref/EFFECTS.md` entries E-0001…E-0003 into `experiment`.
7. `signals` v1, pure code, each row with numbers and the source rows: striking distance (position 8–20 with impressions), low CTR in top 10, no impressions 28 days after publication, two pages on one query, cluster traction, published but not in the Yandex index, AI robot demand per page, robot 404s, tech issues from the crawl, stale source data, `agent_trust_page`, `new_bot_family`.
   Age rule, mandatory for every «share of pages without impressions» number and for the «no impressions» signal: a page counts only if it was published at least 28 days before the END of the data window. On 11.11 a recon number «142 of 223 articles have no impressions» turned out to be an age artifact: 132 of them were younger than a week; of the 61 articles older than 28 days 59 had impressions. The report must show such shares by age cohort (28+ days, 14–27, 7–13, 0–6, after the window).
8. `report`: one self-contained HTML page per week (summary numbers with their windows, cohort table, top signals, data freshness per source) and a short text digest. Plain tables, no design work. No sending anywhere in this stage: files only.
9. CLI: `python3 -m engine.cli collect|crawl|load|signals|report --site lida [--date]` (a thin `seo` wrapper is fine). A launchd plist template `ru.seo-engine.lida.nightly` is written to the repo but NOT installed.

## AI robots and agents
- `access_log.py` classifies AI families into classes kept as data in `playbooks/ai_bots.yaml`: training crawler, search index, user-triggered fetch (ChatGPT-User, Perplexity-User, Claude-User and similar), coding agents. User-Agent alone is not trusted: take the client address from the configured header and mark each request `verified` or `unverified` against the publishers' published IP ranges, stored as dated files under `playbooks/` (fetch them if the network allows, otherwise ship the loader and an empty dated placeholder and say so). Requests for `.env` and similar probes under an AI name are marked as spoofed.
- `bot_day` keeps family, class, verified flag, page, status, per day, with no expiry.
- `ai_panel` (date, model, question id, mentioned yes/no, cited URLs, competitor names, offer phrase found yes/no, raw answer path): schema and loader only. No calls to model APIs in this stage.
- `agent_trust_page`: pages with verified user-triggered fetches or panel citations, ranked. `new_bot_family`: an unknown User-Agent family with growing volume.

## Boundaries
- Do not modify the Lida repo, the live site, launchd, the server or any database. Do not install the plist. No browser. No outside messages. No pushes.
- Do not design UI.

## Checks
- `python3 -m unittest` green: loaders are idempotent (two runs give the same row counts), the freshness check fails on a stale fixture AND on an all-zero fixture, internal User-Agents are excluded, the age rule is tested, a config with another domain runs the whole pipeline on a tiny fixture site (proof of reuse).
- One real run for `lida` into `./.data/lida/`: row counts per table, newest real data date per source, number of signals per rule, the cohort table.
- `grep -ri "lida" engine/` returns nothing.

## Report back
Write `REPORT.md` in the repo root, at most 60 lines: PASS or FAIL per deliverable, row counts, signal counts per rule, the cohort table, newest data date per source, top 10 striking-distance pages with numbers, what was not run live and why, and anything that could not be done with the exact reason. Last line of your final message: `DONE` or `BLOCKED: <reason>`.
