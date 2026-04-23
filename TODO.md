# TODO

Framework-level improvements to recon-og.

## Encrypted secret storage (Chrome-style)

API keys are currently stored in plaintext in `~/.recon-og/keys.db` (SQLite `keys` table, `value` column). Anyone with read access to the home directory — including an attacker who landed limited-user code execution — walks away with every paid API key the user has configured. This is the single biggest local-attack-surface issue in the framework.

**Proposed model (mirrors Chrome's password manager UX):**

1. On first use of `keys add`, prompt the user for a master password (confirm twice).
2. Derive an encryption key with PBKDF2 (or better — Argon2id) from the master password + a per-install salt stored alongside the DB.
3. Encrypt each key value with AES-GCM under that derived key; store ciphertext + nonce + auth tag, not plaintext.
4. On every `recon-og` startup that touches a module requiring keys, prompt once for the master password, decrypt all keys into an in-memory cache, and serve from cache for the rest of the session. Never write plaintext back to disk.
5. Clear the in-memory cache on exit (Python GC + explicit zeroing where practical).

**Nice-to-haves:**

- Integration with OS keychains (libsecret/gnome-keyring on Linux, Keychain on macOS, DPAPI on Windows) as an alternative to prompting every session.
- An `--unlock-from-env` escape hatch for CI/automation (`RECON_OG_MASTER=...`), with a big warning about reduced security.
- Migration: on first upgrade, detect plaintext rows in `keys.db`, prompt for a new master password, encrypt in place.

**Why it matters for bug bounty:**

Hunters typically hoard keys for Shodan, Censys, HackerTarget, SecurityTrails, Chaos, Whoxy, hibp, and more. A single `cat ~/.recon-og/keys.db` via any shell-level compromise (malicious dependency, stolen shell history, shared workstation) exfiltrates thousands of dollars of paid API access and the recon artefacts tied to them.

**Files likely to touch:**

- `recon/core/framework.py` — `get_key`, `add_key`, `_query_keys`, and the `keys.db` initialisation at startup
- `recon-og` entry point — add the master password prompt before module load
- A new `recon/core/vault.py` (or similar) for the encryption primitives
