# Provenance-chain implementation plan

Status: **PAUSED — do not start work yet.** Plan recorded for future
session; resume by reading this file end-to-end, confirming the design
decisions still hold, then beginning Phase 1 on a new branch.

When work begins: branch off `master` as **`provenance-chain`** and stage
each phase as its own commit (or set of commits) on that branch. Land the
phases sequentially; do not jump ahead. Merge to master only after
Phase 2 has been live-tested on a real recon target without regression.

---

## Goal

Track the chain of modules that produced each row in a workspace.
Currently the `module` column records only the leaf inserter — when
permute generates `dev.evolutionfresh.wpengine.com` from a brute_hosts
row that came from an alienvault row that came from pdcloud_associated,
the workspace shows `module='permute'` and the path is invisible.
With this work, the same row also has
`provenance='pdcloud_associated.alienvault.brute_hosts.permute'`.

Primary motivation: faster QA of pollution sources. We spent hours this
session manually correlating modules to figure out where off-domain
hosts came from. A chain column makes that one query.

Secondary motivation: clearer attribution when one source feeds another
via CNAME chains, derivation patterns, or scope expansion.

---

## Design decisions (confirmed)

1. **New column `provenance TEXT NULL`** added to every entity table.
   `module` column unchanged (still records the leaf inserter).
   Two columns, both populated, neither overloaded.

2. **Merge semantics: α (first-discoverer wins).**
   When an opt-in module tries to insert a row that already exists,
   the existing row's `module` and `provenance` stay as-is. The new
   chain is not recorded. Lossy but simple, consistent with how
   `module` already behaves on conflict. Revisit only if the lossiness
   becomes operationally painful.

3. **Hidden from default views.** The `show <table>` and `dashboard`
   commands filter the `provenance` column out at the print layer
   inside `_do_show_*` (not via a generic hidden-columns set). An
   `--all`-style override or an explicit `provenance <table> <key>`
   command can surface it on demand. Direct SQL queries
   (`SELECT host, module, provenance FROM hosts`) always see it.

4. **Module input via tuples.** Modules opt in to receiving provenance
   by setting `'accepts_provenance': True` in their meta dict. When
   set, `module_run` receives tuples `(value, source_module,
   source_provenance)` instead of bare values. When not set,
   `module_run` keeps the current bare-value contract — no breaking
   changes for existing modules.

5. **Pilot scope: `permute` and `brute_hosts` only** in the first
   round. They are the two derivative modules where chain attribution
   matters most and where the "parent" abstraction is cleanest
   (single-input-table, clear single-row-derivation semantics).
   Other modules opt in case-by-case after the pilot proves out.

6. **Unbounded chain length is acceptable.** TEXT column, no truncation.
   Most chains will be 2-4 segments; pathological cases of a dozen are
   tolerable and not worth engineering around.

---

## Phase 1 — schema + non-breaking framework changes

No module behavior changes. Framework gains the *capacity* for
provenance; nothing exercises it yet.

### Implementation
1. `recon/core/framework.py` `_create_db()`: add
   `provenance TEXT` (nullable) to each `CREATE TABLE` statement for:
   domains, companies, netblocks, locations, vulnerabilities, ports,
   hosts, contacts, credentials, leaks, pushpins, profiles,
   repositories.

2. `recon/core/framework.py` `_migrate_db()`: per table, run
   `ALTER TABLE <t> ADD COLUMN provenance TEXT` only if the column
   does not already exist (check via `PRAGMA table_info(<t>)`).
   Idempotent. Non-destructive on existing workspaces.

3. Update every `insert_*` method in `recon/core/framework.py`
   to accept an optional `provenance=None` keyword and include it
   in the inserted row's data dict. Default None; existing call
   sites unchanged.

4. Update `_do_show_*` table printers to filter the `provenance`
   column out of the default render. Add an explicit override
   (suggestion: an option like `show hosts all` or a new
   `show hosts --columns=*` style — exact UI to be decided when
   implementing). Keep the default invocation of `show hosts`
   visually unchanged from today.

5. Add a `provenance <table> <natural_key>` framework command that
   prints the chain for a specific row, optionally formatted as
   a tree. Phase 1 deliverable is the basic flat-string display;
   Phase 3 enriches it.

### Tests (extend `test_framework.py`)
- `provenance` column exists on every entity table after a fresh
  `_create_db()`.
- Migration test: open a sqlite db that has the pre-provenance
  schema, run `_migrate_db`, verify the column was added without
  data loss.
- `insert_hosts(host=..., provenance='x.y')` writes the value to
  the new column.
- `insert_hosts(host=...)` (no provenance arg) leaves the column
  NULL — backwards-compat.
- `show hosts` default invocation does not include "provenance"
  in its printed columns.
- The override invocation does include it.
- `provenance hosts <natural_key>` returns the chain for the named
  row.

### Exit criteria
All Phase 1 tests pass; full existing `test_framework.py` suite
still passes; full marketplace test suite (public + private) still
passes; Phase 1 commits land on `provenance-chain` branch.

---

## Phase 2 — pilot module opt-in (permute, brute_hosts)

Two derivative modules begin emitting provenance.

### Implementation
1. Module dispatcher in `recon/core/module.py` (or wherever the
   `module_run` call site lives — verify when implementing): detect
   `meta.get('accepts_provenance')`. If True, fetch `module` and
   `provenance` columns alongside the natural-key value and pass
   tuples `(value, source_module, source_provenance)` to
   `module_run`. Otherwise, pass bare values exactly as today.

2. Update `recon/domains-hosts/brute_hosts.py`:
   - Add `'accepts_provenance': True` to meta.
   - Iterate input as tuples.
   - Compute `parent_chain = source_provenance or source_module`.
   - On insert: `self.insert_hosts(host=..., provenance=f"{parent_chain}.brute_hosts")`.
   - Same for the CNAME-target insert (preserves the chain through
     the CNAME hop).

3. Update `recon/hosts-hosts/permute.py`:
   - Add `'accepts_provenance': True` to meta.
   - Iterate input as tuples; compute chain the same way.
   - On insert: `provenance=f"{parent_chain}.permute"`.
   - Note: the existing scope-filter in permute (filters input hosts
     to those under in-scope domains) still applies. Provenance
     attribution is only computed for hosts that pass the filter.

### Tests
- Public marketplace `TestPermute` and `TestBruteHosts`:
  - When the input rows carry `source_module='alienvault'`,
    permute's inserts have `provenance='alienvault.permute'`.
  - When the input rows carry `source_provenance='a.b'`,
    permute's inserts have `provenance='a.b.permute'`.
  - Chain composition through brute_hosts → permute pipeline:
    a host that ultimately came from alienvault and was processed
    through brute_hosts and then permute has provenance
    `alienvault.brute_hosts.permute`.
- Framework test: dispatcher passes tuples to opt-in modules and
  bare values to non-opt-in modules.
- Merge-semantics test: when permute tries to re-insert an
  existing host, the existing row's `module` and `provenance`
  stay unchanged (α confirmed).

### Live validation
1. Wipe the starbucks workspace as before.
2. Run the full Haddix script.
3. Spot-check: does
   `SELECT host, module, provenance FROM hosts WHERE provenance IS NOT NULL`
   show sensible chains?
4. Specifically, the previously-noisy hosts under
   `q4web.com` / `office.com` derived through permute should now
   show full chain, making it trivial to identify them as
   permute-derivatives of brute_hosts CNAME targets.

### Exit criteria
All Phase 2 tests pass; live run on a real target shows chains
populating correctly; no regressions in non-opt-in module behavior.

---

## Phase 3 — ergonomics + further opt-ins

Optional follow-ups, not blocking the merge of Phase 2 to master.

1. `provenance --tree <table> <key>`: displays the chain as a
   visual tree, walking each ancestor row and showing its `host` /
   `domain` / etc. alongside the module that produced it. Helps
   answer "what was the actual data lineage?" not just "what
   modules touched this?".

2. Reporting modules: optionally include the provenance column
   when set explicitly in the report config.

3. Additional modules opt in case-by-case: candidates include
   `mx_spf_ip`, `reverse_resolve`, anything else whose output is
   recognizably derived from a single input row.

---

## Risk register

| Risk | Mitigation |
|---|---|
| `_migrate_db` bug corrupts an existing workspace | Phase 1 schema/migration tests run against both fresh and pre-existing-workspace fixtures. Idempotent ALTER TABLE only adds the column if missing. Non-destructive even on the failure paths. |
| Module dispatcher change breaks the hot path of every module run | Phase 1 dispatcher change is a no-op for non-opt-in modules. Phase 2 only flips the flag on two modules. Marketplace test suite re-run after each phase catches regression. |
| `show hosts` print-layer filter masks legitimate column display | Default behavior unchanged from today; override is explicit. If a future column is added that should *also* be hidden, the same filter pattern is reusable. |
| Chain-composition logic has an off-by-one / null-handling bug | Phase 2 unit tests cover the no-provenance, has-provenance, and chain-extension cases explicitly. |
| Long chains break a downstream consumer (reporting, dashboard, etc.) | Default views don't display provenance. Reports that opt to include it must handle TEXT-of-arbitrary-length, which they already must for `notes` fields. |
| Opt-in flag is forgotten / typo'd in a future module | Adding a no-op default keeps the framework from crashing; the consequence is that the chain stops being threaded for that module's outputs. Document in INSTRUCTIONS.md when Phase 2 lands. |
| Upstream recon-ng makes incompatible schema changes that conflict on rebase | Treat the column as a fork-divergence; if rebasing upstream, prefer reapplying Phase 1's schema/migration on top of the upstream change. |

---

## When resuming

1. Read this file top to bottom, including the design decisions.
2. Confirm the design still matches the operator's needs (especially
   the merge semantics and the opt-in flag pattern).
3. Run `test_framework.py` on master and confirm green.
4. `git checkout -b provenance-chain` from master.
5. Begin Phase 1.
6. After each phase, run the full framework + marketplace test
   suites before pushing.
