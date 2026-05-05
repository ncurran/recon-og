"""recon.core.sidedb — schema constants + connection helpers for the
endpoints and apps side-databases.

The side-DBs live outside the workspace tree because multiple workspaces
share one corpus of endpoints / apps (joined back via fqdn / apex). This
module owns:

- Canonical DDL for both side-DBs.
- ``open_endpoints_db(path)`` / ``open_apps_db(path)`` — lazy-create the
  file and bootstrap the schema, return a sqlite3.Connection.
- ``attach_sidedbs(conn, ...)`` — ATTACH the side-DBs to an existing
  workspace connection so a single SQL query can join across them.

Importable from:
- recon-og base (``Framework.endpoints_conn`` / ``apps_conn`` are thin
  wrappers around the module functions here).
- recon-og marketplace modules (collectors / enrichers reach through
  ``BaseModule`` to the same wrappers).
- Out-of-tree scripts (engagement-specific tools like the planned AAD
  landing-page walker at ``~/bug_bounty/tools/azure/aad_assignment_walker.py``)
  that need to read the side-DB without instantiating a framework.
"""

import os
import sqlite3


DEFAULT_ENDPOINTS_DB_PATH = '~/bug_bounty/tools/recon/endpoints.db'
DEFAULT_APPS_DB_PATH = '~/bug_bounty/tools/recon/apps.db'


ENDPOINTS_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS endpoints (
        id                INTEGER PRIMARY KEY,
        fqdn              TEXT NOT NULL,
        apex              TEXT NOT NULL,
        method            TEXT NOT NULL,
        path_template     TEXT NOT NULL,
        operation_id      TEXT,
        discovered_at     TEXT NOT NULL,
        last_verified_at  TEXT,
        confidence        TEXT NOT NULL,
        app_id            INTEGER,
        UNIQUE (fqdn, method, path_template)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_endpoints_apex ON endpoints(apex)',
    'CREATE INDEX IF NOT EXISTS idx_endpoints_apex_method ON endpoints(apex, method)',
    '''CREATE TABLE IF NOT EXISTS endpoint_observations (
        id                  INTEGER PRIMARY KEY,
        endpoint_id         INTEGER NOT NULL REFERENCES endpoints(id),
        source              TEXT NOT NULL,
        source_ref          TEXT,
        observed_at         TEXT NOT NULL,
        raw_url             TEXT,
        http_status         INTEGER,
        content_type        TEXT,
        response_body_hash  TEXT,
        evidence_blob       TEXT,
        UNIQUE (endpoint_id, source, source_ref)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_obs_endpoint ON endpoint_observations(endpoint_id)',
    'CREATE INDEX IF NOT EXISTS idx_obs_source ON endpoint_observations(source)',
    '''CREATE TABLE IF NOT EXISTS endpoint_params (
        id            INTEGER PRIMARY KEY,
        endpoint_id   INTEGER NOT NULL REFERENCES endpoints(id),
        location      TEXT NOT NULL,
        name          TEXT NOT NULL,
        type_hint     TEXT,
        sample_values TEXT,
        required      INTEGER,
        source        TEXT NOT NULL,
        UNIQUE (endpoint_id, location, name, source)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_params_endpoint ON endpoint_params(endpoint_id)',
    '''CREATE TABLE IF NOT EXISTS endpoint_tags (
        endpoint_id INTEGER NOT NULL REFERENCES endpoints(id),
        tag         TEXT NOT NULL,
        source      TEXT,
        PRIMARY KEY (endpoint_id, tag)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_tags_tag ON endpoint_tags(tag)',
)


APPS_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS apps (
        id                INTEGER PRIMARY KEY,
        fqdn              TEXT NOT NULL,
        apex              TEXT NOT NULL,
        path_prefix       TEXT NOT NULL DEFAULT '/',
        app_class         TEXT NOT NULL,
        confidence        TEXT NOT NULL,
        discovered_at     TEXT NOT NULL,
        last_verified_at  TEXT NOT NULL,
        UNIQUE (fqdn, path_prefix, app_class)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_apps_apex ON apps(apex)',
    'CREATE INDEX IF NOT EXISTS idx_apps_class ON apps(app_class)',
    '''CREATE TABLE IF NOT EXISTS app_facts (
        app_id      INTEGER NOT NULL REFERENCES apps(id),
        fact_class  TEXT NOT NULL,
        fact_key    TEXT NOT NULL,
        fact_value  TEXT,
        source      TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY (app_id, fact_class, fact_key)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_facts_class_value ON app_facts(fact_class, fact_value)',
)


def open_endpoints_db(path=None):
    '''Open (and lazily create + bootstrap schema) the endpoints side-DB.
    ``path`` is expanded with ``~`` resolution; falls back to the canonical
    default. Returns a ``sqlite3.Connection`` — caller closes.'''
    return _open(_resolve(path or DEFAULT_ENDPOINTS_DB_PATH), ENDPOINTS_SCHEMA)


def open_apps_db(path=None):
    '''Open (and lazily create + bootstrap schema) the apps side-DB.'''
    return _open(_resolve(path or DEFAULT_APPS_DB_PATH), APPS_SCHEMA)


def attach_sidedbs(conn, endpoints_path=None, apps_path=None,
                   endpoints_alias='endpoints_db', apps_alias='apps_db'):
    '''ATTACH the endpoints + apps side-DBs to an existing sqlite3
    connection (typically a workspace ``data.db``) so a single SQL
    statement can join across schemas.

    Both side-DB files are lazy-created and schema-bootstrapped before
    attach if they don't exist yet — out-of-tree tools don't need a
    separate init step.

    Example:
        conn = sqlite3.connect(workspace_db)
        attach_sidedbs(conn)
        rows = conn.execute(\"\"\"
            SELECT h.host, e.method, e.path_template
            FROM main.hosts h
            JOIN endpoints_db.endpoints e ON e.fqdn = h.host
            WHERE e.confidence = 'documented'
        \"\"\").fetchall()
    '''
    ep_path = _resolve(endpoints_path or DEFAULT_ENDPOINTS_DB_PATH)
    ap_path = _resolve(apps_path or DEFAULT_APPS_DB_PATH)
    open_endpoints_db(ep_path).close()
    open_apps_db(ap_path).close()
    # ATTACH does not bind via parameters; quote the path defensively.
    conn.execute(f"ATTACH DATABASE '{_quote(ep_path)}' AS {_ident(endpoints_alias)}")
    conn.execute(f"ATTACH DATABASE '{_quote(ap_path)}' AS {_ident(apps_alias)}")


# ── internal ─────────────────────────────────────────────────────────────────

def _resolve(path):
    return os.path.expanduser(path)


def _open(path, schema):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    for stmt in schema:
        conn.execute(stmt)
    conn.commit()
    return conn


def _quote(path):
    return path.replace("'", "''")


def _ident(name):
    if not name.replace('_', '').isalnum():
        raise ValueError(f'invalid SQL identifier: {name!r}')
    return name
