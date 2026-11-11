import sqlite3
from pathlib import Path


def connect(cfg):
    db = sqlite3.connect(cfg['data'] / 'seo.sqlite')
    db.row_factory = sqlite3.Row
    db.executescript(Path(__file__).with_name('schema.sql').read_text())
    return db


def upsert(db, table, **values):
    # All identifiers are engine constants, never user input.
    keys = ','.join('"'+k+'"' for k in values)
    updates = ','.join('"'+k+'"=excluded."'+k+'"' for k in values)
    db.execute(f'INSERT INTO {table} ({keys}) VALUES ({",".join("?" for _ in values)}) ON CONFLICT DO UPDATE SET {updates}', tuple(values.values()))


def counts(db):
    return {r[0]: db.execute('SELECT count(*) FROM "'+r[0]+'"').fetchone()[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()}
