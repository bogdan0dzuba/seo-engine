import sqlite3
from pathlib import Path


def connect(cfg):
    db = sqlite3.connect(cfg['data'] / 'seo.sqlite')
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    # One transaction preserves round-one observations and provenance rowids.
    migrations = []
    old = [r[1] for r in db.execute('PRAGMA table_info(stat_registry)')]
    if old and 'snapshot' in old:
        migrations.append('ALTER TABLE stat_registry RENAME TO source_status;')
    bot_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='bot_day'").fetchone()
    migrate_bots = bot_sql and 'unavailable' not in bot_sql[0]
    if migrate_bots:
        migrations.append('ALTER TABLE bot_day RENAME TO bot_day_v1;')
    migrations.append(Path(__file__).with_name('schema.sql').read_text())
    if migrate_bots:
        migrations.extend(['INSERT INTO bot_day (rowid,date,family,class,verified,page,status,hits,special,evidence) SELECT rowid,* FROM bot_day_v1;',
                           'DROP TABLE bot_day_v1;'])
    db.executescript('BEGIN;\n'+'\n'.join(migrations)+'\nCOMMIT;')
    return db


def upsert(db, table, **values):
    # All identifiers are engine constants, never user input.
    keys = ','.join('"'+k+'"' for k in values)
    updates = ','.join('"'+k+'"=excluded."'+k+'"' for k in values)
    db.execute(f'INSERT INTO {table} ({keys}) VALUES ({",".join("?" for _ in values)}) ON CONFLICT DO UPDATE SET {updates}', tuple(values.values()))


def counts(db):
    return {r[0]: db.execute('SELECT count(*) FROM "'+r[0]+'"').fetchone()[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()}
