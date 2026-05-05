#!/usr/bin/env python3
"""
recon-og framework test harness.

Pins the framework behaviour that modules and resource scripts rely on:
workspace lifecycle, the insert_* API for every entity table, key storage,
module loading, and the resource-script (.rc) execution path.

These tests use a TMP HOME so the user's real ~/.recon-og state is never
touched. Each test class runs against a freshly-initialised tree so they
can run in any order.

    python3 -m pytest test_framework.py -v
"""

import os
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import textwrap
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_RECON_OG = os.path.join(_REPO, 'recon-og')


class _IsolatedHome:
    """Context manager that runs recon-og under a tmp HOME so the real
    ~/.recon-og state is never modified. Returns the tmp dir path."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix='recon_og_test_')
        self.env = os.environ.copy()
        self.env['HOME'] = self.tmp
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_rc(self, rc_text, workspace='default', extra_args=()):
        """Write rc_text to a temp file and run recon-og against it."""
        rc = os.path.join(self.tmp, 'test.rc')
        with open(rc, 'w') as f:
            f.write(textwrap.dedent(rc_text).strip() + '\nexit\n')
        cmd = [
            sys.executable, _RECON_OG,
            '-w', workspace, '-r', rc,
            '--no-version', '--no-analytics', '--no-marketplace',
        ] + list(extra_args)
        return subprocess.run(
            cmd, env=self.env, capture_output=True, text=True, timeout=60,
        )

    def workspace_db(self, workspace='default'):
        return os.path.join(self.tmp, '.recon-og', 'workspaces', workspace, 'data.db')

    def keys_db(self):
        return os.path.join(self.tmp, '.recon-og', 'keys.db')


# ═══════════════════════════════════════════════════════════════════════════════
# Workspace lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkspaceLifecycle(unittest.TestCase):
    """Workspaces are SQLite databases under ~/.recon-og/workspaces/<name>/.
    Modules depend on the framework auto-creating the workspace on first use,
    populating the canonical schema, and being able to load it again later."""

    def test_workspace_created_on_first_run(self):
        with _IsolatedHome() as h:
            r = h.run_rc('# noop')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertTrue(os.path.exists(h.workspace_db('default')))

    def test_workspace_create_command(self):
        with _IsolatedHome() as h:
            h.run_rc('workspaces create acme.com')
            self.assertTrue(os.path.exists(h.workspace_db('acme.com')))

    def test_workspace_persists_across_runs(self):
        with _IsolatedHome() as h:
            h.run_rc('db insert domains acme.com~', workspace='acme.com')
            r2 = h.run_rc('query SELECT domain FROM domains', workspace='acme.com')
            self.assertEqual(r2.returncode, 0, msg=r2.stderr)
            self.assertIn('acme.com', r2.stdout)

    def test_workspace_remove(self):
        with _IsolatedHome() as h:
            h.run_rc('# init', workspace='throwaway')
            self.assertTrue(os.path.exists(h.workspace_db('throwaway')))
            # Can't remove the active workspace, so switch to default first.
            h.run_rc('workspaces remove throwaway', workspace='default')
            self.assertFalse(os.path.exists(h.workspace_db('throwaway')))


# ═══════════════════════════════════════════════════════════════════════════════
# Schema — every entity table the marketplace depends on must exist
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchema(unittest.TestCase):
    """Module authors rely on the canonical table set being present and on
    each table having the columns they write to. Locking this down here
    catches accidental schema regressions in framework refactors."""

    EXPECTED_TABLES = {
        'domains', 'companies', 'netblocks', 'locations',
        'vulnerabilities', 'ports', 'hosts', 'contacts',
        'credentials', 'leaks', 'pushpins', 'profiles', 'repositories',
    }

    EXPECTED_COLUMNS = {
        'domains':        {'domain', 'notes', 'module'},
        'companies':      {'company', 'description', 'notes', 'module'},
        'hosts':          {'host', 'ip_address', 'region', 'country',
                           'latitude', 'longitude', 'notes', 'module'},
        'ports':          {'ip_address', 'host', 'port', 'protocol',
                           'banner', 'notes', 'module'},
        'vulnerabilities': {'host', 'reference', 'example', 'publish_date',
                            'category', 'status', 'notes', 'module'},
        'credentials':    {'username', 'password', 'hash', 'type',
                           'leak', 'notes', 'module'},
        'contacts':       {'first_name', 'middle_name', 'last_name', 'email',
                           'title', 'region', 'country', 'phone',
                           'notes', 'module'},
        'netblocks':      {'netblock', 'notes', 'module'},
    }

    def test_canonical_tables_exist(self):
        with _IsolatedHome() as h:
            h.run_rc('# init')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            tables = {r[0] for r in rows}
            missing = self.EXPECTED_TABLES - tables
            self.assertEqual(missing, set(), msg=f"missing tables: {missing}")

    def test_table_column_layout(self):
        """Every column the marketplace's insert_* helpers and SELECTs depend
        on must exist on each table. Catches column renames at schema level."""
        with _IsolatedHome() as h:
            h.run_rc('# init')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                for table, expected in self.EXPECTED_COLUMNS.items():
                    with self.subTest(table=table):
                        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                        missing = expected - cols
                        self.assertEqual(
                            missing, set(),
                            msg=f"{table}: missing columns {missing} (have {cols})",
                        )


# ═══════════════════════════════════════════════════════════════════════════════
# Insert API — exercise every insert_*() entrypoint via `db insert`
# ═══════════════════════════════════════════════════════════════════════════════

class TestInsertAPI(unittest.TestCase):
    """`db insert <table> <values>` is the REPL-level wrapper around the
    insert_* methods that modules call. Pinning these prevents silent
    breakage of the call signatures or column ordering. Values are
    tilde-separated (the framework's documented column delimiter)."""

    def _insert_and_query(self, table, value_string, expect_in_table):
        """Insert into `table` with the tilde-separated value string,
        then assert the expected row appears."""
        with _IsolatedHome() as h:
            r = h.run_rc(f'db insert {table} {value_string}')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            with sqlite3.connect(h.workspace_db('default')) as conn:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            self.assertTrue(rows, msg=f"{table} is empty after insert")
            self.assertIn(expect_in_table, str(rows[0]))

    def test_insert_domain(self):
        self._insert_and_query('domains', 'acme.com~note', 'acme.com')

    def test_insert_company(self):
        self._insert_and_query('companies', 'Acme Corp~widget mfg~', 'Acme Corp')

    def test_insert_host(self):
        self._insert_and_query(
            'hosts', 'mail.acme.com~10.0.0.1~~~~~',  # host, ip, region, country, lat, long, notes
            'mail.acme.com',
        )

    def test_insert_netblock(self):
        self._insert_and_query('netblocks', '10.0.0.0/24~', '10.0.0.0/24')

    def test_insert_port(self):
        self._insert_and_query('ports', '10.0.0.1~h.acme.com~443~tcp~~',
                               '443')

    def test_insert_credentials(self):
        # username, password, hash, type, leak, notes
        self._insert_and_query(
            'credentials', 'alice~~5f4dcc3b5aa765d61d8327deb882cf99~MD5~~',
            '5f4dcc3b5aa765d61d8327deb882cf99',
        )

    def test_insert_contact(self):
        # first, middle, last, email, title, region, country, phone, notes
        self._insert_and_query(
            'contacts', 'Alice~~Doe~alice@acme.com~CTO~~US~~',
            'alice@acme.com',
        )

    def test_insert_vulnerability(self):
        # host, reference, example, publish_date, category, status, notes
        self._insert_and_query(
            'vulnerabilities', 'mail.acme.com~CVE-2024-1234~POC~~XSS~Vulnerable~',
            'CVE-2024-1234',
        )

    def test_module_column_set_to_user_defined(self):
        """When inserts come from `db insert` (not a module), the module
        column should be 'user_defined'. Several modules depend on filtering
        by module to find newly-discovered vs. seeded rows."""
        with _IsolatedHome() as h:
            h.run_rc('db insert domains acme.com~')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                row = conn.execute(
                    "SELECT module FROM domains WHERE domain='acme.com'"
                ).fetchone()
            self.assertEqual(row[0], 'user_defined')


# ═══════════════════════════════════════════════════════════════════════════════
# Key storage — keys.db is shared across workspaces
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeyStorage(unittest.TestCase):
    """Keys live in a single SQLite db at ~/.recon-og/keys.db with schema
    keys(name TEXT PRIMARY KEY, value TEXT). Modules read via self.get_key()."""

    def test_keys_add_persists_to_keys_db(self):
        with _IsolatedHome() as h:
            r = h.run_rc('keys add example_key abc123xyz')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertTrue(os.path.exists(h.keys_db()))
            with sqlite3.connect(h.keys_db()) as conn:
                row = conn.execute(
                    "SELECT value FROM keys WHERE name='example_key'"
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 'abc123xyz')

    def test_keys_remove(self):
        with _IsolatedHome() as h:
            h.run_rc('keys add example_key abc123xyz')
            h.run_rc('keys remove example_key')
            with sqlite3.connect(h.keys_db()) as conn:
                row = conn.execute(
                    "SELECT value FROM keys WHERE name='example_key'"
                ).fetchone()
            self.assertIsNone(row)

    def test_keys_persist_across_workspaces(self):
        """Keys are user-global, not per-workspace."""
        with _IsolatedHome() as h:
            h.run_rc('keys add example_key abc', workspace='ws1')
            with sqlite3.connect(h.keys_db()) as conn:
                row = conn.execute(
                    "SELECT value FROM keys WHERE name='example_key'"
                ).fetchone()
            self.assertEqual(row[0], 'abc')


# ═══════════════════════════════════════════════════════════════════════════════
# Resource script (.rc) parser — REPL command handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestResourceScript(unittest.TestCase):
    """The framework's REPL line-parser is what every .rc script runs through.
    Multi-command sequences, blank lines, and comment handling all matter."""

    def test_multiple_commands_in_sequence(self):
        with _IsolatedHome() as h:
            r = h.run_rc('''
                db insert domains a.com~
                db insert domains b.com~
                db insert domains c.com~
            ''')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            with sqlite3.connect(h.workspace_db('default')) as conn:
                rows = conn.execute(
                    "SELECT domain FROM domains ORDER BY domain"
                ).fetchall()
            self.assertEqual([r[0] for r in rows], ['a.com', 'b.com', 'c.com'])

    def test_blank_lines_ignored(self):
        with _IsolatedHome() as h:
            r = h.run_rc('''

                db insert domains acme.com~

                db insert domains widget.com~

            ''')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            with sqlite3.connect(h.workspace_db('default')) as conn:
                count = conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
            self.assertEqual(count, 2)

    def test_hash_comments_currently_emit_invalid_command(self):
        """Documents existing behaviour rather than asserting the design we'd
        like — currently `# foo` lines hit the REPL as literal commands and
        emit '[!] Invalid command: # foo'. If/when the framework adds proper
        comment handling, flip this assertion. The haddix_recon.sh wrapper
        strips comments before invocation as a workaround."""
        with _IsolatedHome() as h:
            r = h.run_rc('''
                # this is a comment
                db insert domains acme.com~
            ''')
            # Insert still succeeds even with the noisy warning.
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            with sqlite3.connect(h.workspace_db('default')) as conn:
                count = conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
            self.assertEqual(count, 1)
            # Document that the comment line produces an Invalid command warning.
            self.assertIn('Invalid command', r.stdout + r.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# Module loading — every installed module must import without SyntaxError
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleLoading(unittest.TestCase):
    """When recon-og starts, it walks ~/.recon-og/modules/recon/* and imports
    every .py file. If any file has a SyntaxError or ImportError, the module
    count line in the banner reflects the broken module being skipped — but
    the user has no way to know without checking. Pin module-loading
    cleanliness here so a regression is caught in CI rather than at runtime."""

    def _install_modules_to(self, home_dir, source_dirs):
        """Symlink module files from each source_dir into the home's
        ~/.recon-og/modules/recon/ tree, mirroring what install.sh does."""
        target = os.path.join(home_dir, '.recon-og', 'modules')
        for source in source_dirs:
            if not os.path.isdir(source):
                continue
            for root, _, files in os.walk(os.path.join(source, 'recon')):
                rel = os.path.relpath(root, source)
                os.makedirs(os.path.join(target, rel), exist_ok=True)
                for f in files:
                    if not f.endswith('.py') or f == '__init__.py':
                        continue
                    src = os.path.join(root, f)
                    dst = os.path.join(target, rel, f)
                    if not os.path.exists(dst):
                        os.symlink(src, dst)

    def test_marketplace_modules_all_load(self):
        """Run a noop rc with both marketplace repos symlinked in.
        recon-og prints '[N] Recon modules' on startup; failure to import
        a module would cause it to be dropped (count goes down silently),
        but the framework also surfaces SyntaxWarning errors to stderr.
        Assert: no module-load errors in stderr."""
        marketplace_dirs = [
            os.path.expanduser('~/code/github_com_ncurran/recon-og-marketplace'),
            os.path.expanduser('~/code/github_com_ncurran/recon-og-marketplace-private'),
        ]
        marketplace_dirs = [d for d in marketplace_dirs if os.path.isdir(d)]
        if not marketplace_dirs:
            self.skipTest("no marketplace repos found at expected paths")

        with _IsolatedHome() as h:
            os.makedirs(os.path.join(h.tmp, '.recon-og'), exist_ok=True)
            self._install_modules_to(h.tmp, marketplace_dirs)
            r = h.run_rc('# noop')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Errors a broken module would raise — surfaced by the framework
            # at load time. We ignore "key not set" warnings (those are
            # information, not errors).
            stderr_lines = [
                line for line in (r.stderr or '').split('\n')
                if line.strip()
                and 'key not set' not in line
                and 'will likely fail at runtime' not in line
            ]
            problems = [
                line for line in stderr_lines
                if any(token in line for token in ('SyntaxError', 'ImportError',
                                                    'Traceback', 'failed to load',
                                                    'SyntaxWarning'))
            ]
            self.assertEqual(problems, [],
                             msg=f"module-load problems:\n  " + "\n  ".join(problems))


# ═══════════════════════════════════════════════════════════════════════════════
# Sanity: starting/exiting the framework cleanly on a known-good config
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrameworkBoot(unittest.TestCase):

    def test_recon_og_exits_zero_on_clean_rc(self):
        with _IsolatedHome() as h:
            r = h.run_rc('# noop')
            self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr}")

    def test_recon_og_help_does_not_crash(self):
        proc = subprocess.run(
            [sys.executable, _RECON_OG, '--help'],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn('-w', proc.stdout)
        self.assertIn('-r', proc.stdout)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — provenance column (schema, migration, insert API, show filter)
# See PROVENANCE_PLAN.md for the design.
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvenanceSchema(unittest.TestCase):
    """Phase 1: every entity table has a `provenance TEXT` column on a fresh
    workspace, and existing pre-Phase-1 workspaces get the column added on
    open (idempotent migration, non-destructive)."""

    PROVENANCE_TABLES = (
        'domains', 'companies', 'netblocks', 'locations', 'vulnerabilities',
        'ports', 'hosts', 'contacts', 'credentials', 'leaks', 'pushpins',
        'profiles', 'repositories',
    )

    def test_provenance_column_on_fresh_workspace(self):
        with _IsolatedHome() as h:
            h.run_rc('# init')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                for table in self.PROVENANCE_TABLES:
                    with self.subTest(table=table):
                        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                        self.assertIn('provenance', cols, msg=f"{table} missing provenance column")

    def test_user_version_bumped_to_11(self):
        with _IsolatedHome() as h:
            h.run_rc('# init')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                version = conn.execute('PRAGMA user_version').fetchone()[0]
            self.assertEqual(version, 11)

    def test_migration_from_v10_adds_provenance_column(self):
        """Open a workspace that's on schema v10 (no provenance) and verify
        the migration adds the column without dropping any existing data."""
        with _IsolatedHome() as h:
            os.makedirs(os.path.join(h.tmp, '.recon-og', 'workspaces', 'legacy'))
            db = h.workspace_db('legacy')
            # Fabricate a v10 workspace: same schema as current minus provenance.
            with sqlite3.connect(db) as conn:
                conn.executescript('''
                    CREATE TABLE domains (domain TEXT, notes TEXT, module TEXT);
                    CREATE TABLE companies (company TEXT, description TEXT, notes TEXT, module TEXT);
                    CREATE TABLE netblocks (netblock TEXT, notes TEXT, module TEXT);
                    CREATE TABLE locations (latitude TEXT, longitude TEXT, street_address TEXT, notes TEXT, module TEXT);
                    CREATE TABLE vulnerabilities (host TEXT, reference TEXT, example TEXT, publish_date TEXT, category TEXT, status TEXT, notes TEXT, module TEXT);
                    CREATE TABLE ports (ip_address TEXT, host TEXT, port TEXT, protocol TEXT, banner TEXT, notes TEXT, module TEXT);
                    CREATE TABLE hosts (host TEXT, ip_address TEXT, region TEXT, country TEXT, latitude TEXT, longitude TEXT, notes TEXT, module TEXT);
                    CREATE TABLE contacts (first_name TEXT, middle_name TEXT, last_name TEXT, email TEXT, title TEXT, region TEXT, country TEXT, phone TEXT, notes TEXT, module TEXT);
                    CREATE TABLE credentials (username TEXT, password TEXT, hash TEXT, type TEXT, leak TEXT, notes TEXT, module TEXT);
                    CREATE TABLE leaks (leak_id TEXT, description TEXT, source_refs TEXT, leak_type TEXT, title TEXT, import_date TEXT, leak_date TEXT, attackers TEXT, num_entries TEXT, score TEXT, num_domains_affected TEXT, attack_method TEXT, target_industries TEXT, password_hash TEXT, password_type TEXT, targets TEXT, media_refs TEXT, notes TEXT, module TEXT);
                    CREATE TABLE pushpins (source TEXT, screen_name TEXT, profile_name TEXT, profile_url TEXT, media_url TEXT, thumb_url TEXT, message TEXT, latitude TEXT, longitude TEXT, time TEXT, notes TEXT, module TEXT);
                    CREATE TABLE profiles (username TEXT, resource TEXT, url TEXT, category TEXT, notes TEXT, module TEXT);
                    CREATE TABLE repositories (name TEXT, owner TEXT, description TEXT, resource TEXT, category TEXT, url TEXT, notes TEXT, module TEXT);
                    CREATE TABLE dashboard (module TEXT PRIMARY KEY, runs INT);
                    PRAGMA user_version = 10;
                    -- Pre-existing rows must survive the migration intact.
                    INSERT INTO domains (domain, module) VALUES ('legacy.com', 'user_defined');
                    INSERT INTO hosts (host, ip_address, module) VALUES ('mail.legacy.com', '10.0.0.1', 'brute_hosts');
                ''')

            # Trigger migration by opening the workspace.
            r = h.run_rc('# trigger-migration', workspace='legacy')
            self.assertEqual(r.returncode, 0, msg=r.stderr)

            with sqlite3.connect(db) as conn:
                # Schema upgraded
                ver = conn.execute('PRAGMA user_version').fetchone()[0]
                self.assertEqual(ver, 11)
                # Provenance column present on every entity table now
                for table in self.PROVENANCE_TABLES:
                    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                    self.assertIn('provenance', cols, msg=f"{table} missing provenance after migration")
                # Pre-existing rows intact, with NULL provenance (no chain known)
                row = conn.execute(
                    "SELECT domain, module, provenance FROM domains WHERE domain='legacy.com'"
                ).fetchone()
                self.assertEqual(row, ('legacy.com', 'user_defined', None))
                row = conn.execute(
                    "SELECT host, ip_address, module, provenance FROM hosts WHERE host='mail.legacy.com'"
                ).fetchone()
                self.assertEqual(row, ('mail.legacy.com', '10.0.0.1', 'brute_hosts', None))


class TestProvenanceShowFilter(unittest.TestCase):
    """Phase 1: `show <table>` excludes the provenance column by default;
    `show <table> all` includes it."""

    def test_default_show_does_not_include_provenance(self):
        with _IsolatedHome() as h:
            h.run_rc('db insert domains acme.com~')
            r = h.run_rc('show domains')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Header line lists column names — assert provenance not there.
            self.assertNotIn('provenance', r.stdout.lower())

    def test_show_all_includes_provenance(self):
        with _IsolatedHome() as h:
            h.run_rc('db insert domains acme.com~')
            r = h.run_rc('show domains all')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('provenance', r.stdout.lower())


class TestProvenanceCommand(unittest.TestCase):
    """Phase 1: `provenance <table> <key>` looks up the row and prints
    its chain (or its module name if no chain is recorded)."""

    def test_provenance_for_user_defined_row_prints_module_name(self):
        """Without an opt-in module having written a chain yet, the
        provenance lookup falls back to printing the module column."""
        with _IsolatedHome() as h:
            h.run_rc('db insert domains acme.com~')
            r = h.run_rc('provenance domains acme.com')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('user_defined', r.stdout)

    def test_provenance_no_match(self):
        with _IsolatedHome() as h:
            r = h.run_rc('provenance domains nonexistent.com')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("No 'domains' row", r.stdout)

    def test_provenance_unknown_table(self):
        with _IsolatedHome() as h:
            r = h.run_rc('provenance not_a_table some.value')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('No such table', r.stdout + r.stderr)


class TestProvenanceInsertAPI(unittest.TestCase):
    """Phase 1: framework's insert_*() methods accept a provenance kwarg
    and write it to the new column. Verified by writing a tiny rc that
    drops directly into the SQLite db with raw INSERT statements that
    exercise the schema (the public-facing path is via modules opting in
    in Phase 2)."""

    def test_provenance_column_writable_via_raw_insert(self):
        with _IsolatedHome() as h:
            h.run_rc('# init')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                conn.execute(
                    "INSERT INTO hosts (host, ip_address, module, provenance) VALUES (?,?,?,?)",
                    ('mail.acme.com', '10.0.0.1', 'permute', 'alienvault.brute_hosts.permute'),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT module, provenance FROM hosts WHERE host='mail.acme.com'"
                ).fetchone()
            self.assertEqual(row, ('permute', 'alienvault.brute_hosts.permute'))

    def test_provenance_lookup_returns_chain(self):
        with _IsolatedHome() as h:
            h.run_rc('# init')
            with sqlite3.connect(h.workspace_db('default')) as conn:
                conn.execute(
                    "INSERT INTO hosts (host, ip_address, module, provenance) VALUES (?,?,?,?)",
                    ('mail.acme.com', '10.0.0.1', 'permute', 'alienvault.brute_hosts.permute'),
                )
                conn.commit()
            r = h.run_rc('provenance hosts mail.acme.com')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('alienvault.brute_hosts.permute', r.stdout)


# ═══════════════════════════════════════════════════════════════════════════════
# Side-databases (endpoints + apps)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSideDatabases(unittest.TestCase):
    """Schema bootstrap, connection helpers, and `show endpoints` / `show apps`
    CLI affordances exposed by the framework. The collectors that write into
    these DBs live in the marketplace; the framework only owns the surface."""

    def _ep_db_path(self, h):
        return os.path.join(h.tmp, 'endpoints.db')

    def _apps_db_path(self, h):
        return os.path.join(h.tmp, 'apps.db')

    def _set_paths_rc(self, h):
        # Always set the side-DB paths inside the tmp HOME so tests don't
        # touch ~/bug_bounty/tools/recon. Returns rc-script lines that
        # callers can prepend to their own commands.
        return (
            f"options set ENDPOINTS_DB_PATH {self._ep_db_path(h)}\n"
            f"options set APPS_DB_PATH {self._apps_db_path(h)}\n"
        )

    def _seed_endpoint(self, db_path, fqdn='api.example.com', apex='example.com',
                       method='GET', path='/users'):
        """Open the side-DB (auto-bootstrapping schema by importing the
        framework's constants) and INSERT one endpoint row."""
        # We import the canonical schema from the framework module under test
        # so the test stays in sync with whatever shape the framework creates.
        sys.path.insert(0, _REPO)
        try:
            from recon.core import sidedb
        finally:
            sys.path.pop(0)
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            for stmt in sidedb.ENDPOINTS_SCHEMA:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO endpoints (fqdn, apex, method, path_template, "
                "discovered_at, last_verified_at, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, 'observed')",
                (fqdn, apex, method, path,
                 '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
            )
            conn.commit()
        finally:
            conn.close()

    def _seed_app(self, db_path, fqdn='wordpress.example.com', apex='example.com',
                  app_class='wordpress'):
        sys.path.insert(0, _REPO)
        try:
            from recon.core import sidedb
        finally:
            sys.path.pop(0)
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            for stmt in sidedb.APPS_SCHEMA:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO apps (fqdn, apex, path_prefix, app_class, "
                "confidence, discovered_at, last_verified_at) "
                "VALUES (?, ?, '/', ?, 'fingerprinted', "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (fqdn, apex, app_class),
            )
            conn.commit()
        finally:
            conn.close()

    # ── global option registration ───────────────────────────────────────────

    def test_endpoints_db_path_option_registered_with_default(self):
        with _IsolatedHome() as h:
            r = h.run_rc('options list')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('ENDPOINTS_DB_PATH', r.stdout)
            self.assertIn('endpoints.db', r.stdout)

    def test_apps_db_path_option_registered_with_default(self):
        with _IsolatedHome() as h:
            r = h.run_rc('options list')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('APPS_DB_PATH', r.stdout)
            self.assertIn('apps.db', r.stdout)

    def test_options_set_endpoints_db_path_takes_effect(self):
        with _IsolatedHome() as h:
            target = os.path.join(h.tmp, 'custom_endpoints.db')
            rc = (
                f"options set ENDPOINTS_DB_PATH {target}\n"
                "options list\n"
            )
            r = h.run_rc(rc)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn(target, r.stdout)

    # ── show endpoints / show apps (empty + populated) ───────────────────────

    def test_show_endpoints_when_no_db_present(self):
        with _IsolatedHome() as h:
            rc = self._set_paths_rc(h) + 'show endpoints\n'
            r = h.run_rc(rc)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Nothing written, so the framework should report it.
            self.assertIn('No side-database', r.stdout)

    def test_show_endpoints_lists_seeded_rows_scoped_to_apex(self):
        with _IsolatedHome() as h:
            # Seed a workspace-domains row that scopes the show output.
            # And an out-of-scope endpoint that must not appear.
            ep_db = self._ep_db_path(h)
            self._seed_endpoint(ep_db,
                                fqdn='api.example.com', apex='example.com',
                                path='/in-scope')
            self._seed_endpoint(ep_db,
                                fqdn='api.other.com', apex='other.com',
                                path='/out-of-scope')
            rc = (
                'db insert domains example.com~\n'
                + self._set_paths_rc(h)
                + 'show endpoints\n'
            )
            r = h.run_rc(rc)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('api.example.com', r.stdout)
            self.assertIn('/in-scope', r.stdout)
            self.assertNotIn('api.other.com', r.stdout)
            self.assertNotIn('/out-of-scope', r.stdout)

    def test_show_endpoints_no_seeded_apex_shows_everything(self):
        # When the workspace has no domains seeded, fall back to showing
        # all rows so first-run inspection works.
        with _IsolatedHome() as h:
            self._seed_endpoint(self._ep_db_path(h),
                                fqdn='api.example.com', apex='example.com',
                                path='/no-scope')
            rc = self._set_paths_rc(h) + 'show endpoints\n'
            r = h.run_rc(rc)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('api.example.com', r.stdout)

    def test_show_apps_lists_seeded_rows(self):
        with _IsolatedHome() as h:
            self._seed_app(self._apps_db_path(h),
                           fqdn='wordpress.example.com', apex='example.com',
                           app_class='wordpress')
            rc = (
                'db insert domains example.com~\n'
                + self._set_paths_rc(h)
                + 'show apps\n'
            )
            r = h.run_rc(rc)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('wordpress.example.com', r.stdout)
            self.assertIn('wordpress', r.stdout)

    # ── schema correctness check ─────────────────────────────────────────────

    def test_sidedb_module_importable_for_out_of_tree_tools(self):
        # The walker / post-auth scripts at ~/bug_bounty/tools/azure/* are
        # not modules — they instantiate sqlite3 directly. They must be able
        # to import the schema + open helpers without touching Framework.
        sys.path.insert(0, _REPO)
        try:
            from recon.core import sidedb
        finally:
            sys.path.pop(0)
        # Public surface required by out-of-tree consumers.
        for name in (
            'open_endpoints_db', 'open_apps_db', 'attach_sidedbs',
            'ENDPOINTS_SCHEMA', 'APPS_SCHEMA',
            'DEFAULT_ENDPOINTS_DB_PATH', 'DEFAULT_APPS_DB_PATH',
        ):
            self.assertTrue(hasattr(sidedb, name),
                            f'recon.core.sidedb missing public name: {name}')

    def test_attach_sidedbs_enables_cross_schema_join(self):
        # Simulate the AAD walker's intended usage: open the workspace DB,
        # ATTACH the side-DBs, JOIN across schemas in one query.
        sys.path.insert(0, _REPO)
        try:
            from recon.core import sidedb
        finally:
            sys.path.pop(0)

        with _IsolatedHome() as h:
            # Spin up a workspace + seed a host so main.hosts has a row.
            r = h.run_rc('db insert hosts api.example.com~~~~~~')
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            workspace_db = h.workspace_db('default')

            # Seed the side-DBs at the same paths the framework would use.
            ep_path = self._ep_db_path(h)
            ap_path = self._apps_db_path(h)
            self._seed_endpoint(ep_path,
                                fqdn='api.example.com', apex='example.com',
                                path='/v1/users')
            self._seed_app(ap_path,
                           fqdn='api.example.com', apex='example.com',
                           app_class='aad_app')

            # Out-of-tree usage path — no Framework instance, just sqlite3
            # + the public sidedb module.
            conn = sqlite3.connect(workspace_db)
            try:
                sidedb.attach_sidedbs(conn, endpoints_path=ep_path, apps_path=ap_path)
                # JOIN across main.hosts and endpoints_db.endpoints.
                rows = conn.execute("""
                    SELECT h.host, e.method, e.path_template
                    FROM main.hosts h
                    JOIN endpoints_db.endpoints e ON e.fqdn = h.host
                """).fetchall()
                self.assertIn(('api.example.com', 'GET', '/v1/users'), rows)
                # Apps schema attached too.
                rows = conn.execute(
                    "SELECT fqdn, app_class FROM apps_db.apps"
                ).fetchall()
                self.assertIn(('api.example.com', 'aad_app'), rows)
            finally:
                conn.close()

    def test_attach_sidedbs_creates_missing_files(self):
        # If the side-DB files don't exist yet, attach_sidedbs must create
        # them with the correct schema before attaching — out-of-tree tools
        # won't always have run a collector first.
        sys.path.insert(0, _REPO)
        try:
            from recon.core import sidedb
        finally:
            sys.path.pop(0)
        with _IsolatedHome() as h:
            ep_path = os.path.join(h.tmp, 'fresh_endpoints.db')
            ap_path = os.path.join(h.tmp, 'fresh_apps.db')
            self.assertFalse(os.path.exists(ep_path))
            self.assertFalse(os.path.exists(ap_path))

            # Throwaway in-memory main DB just to give attach_sidedbs a target.
            conn = sqlite3.connect(':memory:')
            try:
                sidedb.attach_sidedbs(conn, endpoints_path=ep_path, apps_path=ap_path)
                # Files now exist with schema in place.
                self.assertTrue(os.path.exists(ep_path))
                self.assertTrue(os.path.exists(ap_path))
                # Tables resolvable through the attached aliases.
                rows = conn.execute(
                    "SELECT name FROM endpoints_db.sqlite_master WHERE type='table'"
                ).fetchall()
                names = {r[0] for r in rows}
                for required in ('endpoints', 'endpoint_observations',
                                 'endpoint_params', 'endpoint_tags'):
                    self.assertIn(required, names)
            finally:
                conn.close()

    def test_schema_has_expected_tables(self):
        # Lift the schema directly from the framework module and confirm that
        # all four endpoint tables (and the two apps tables) come up in a
        # fresh DB created via the helpers.
        sys.path.insert(0, _REPO)
        try:
            from recon.core import sidedb
        finally:
            sys.path.pop(0)
        # Confirm the schema constant lists every table the marketplace
        # collectors and enrichers depend on.
        ddl_blob = '\n'.join(sidedb.ENDPOINTS_SCHEMA + sidedb.APPS_SCHEMA)
        for table in (
            'endpoints', 'endpoint_observations',
            'endpoint_params', 'endpoint_tags',
            'apps', 'app_facts',
        ):
            self.assertIn(f'CREATE TABLE IF NOT EXISTS {table}', ddl_blob)


if __name__ == '__main__':
    unittest.main()
