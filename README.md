# recon-og

Grandma's favorite recon tool, now featuring AI vibes! A fork of the [Recon-ng Framework](https://github.com/lanmaster53/recon-ng) with updated modules and bug fixes; we stand on the shoulders of ~~giants~~ original gangsters.

## Installation

```bash
git clone git@github.com:ncurran/recon-og.git
cd recon-og
pip install -r REQUIREMENTS
sudo bash install.sh
```

Then launch with:

```bash
recon-og
```

---

recon-og is a full-featured reconnaissance framework designed to provide a powerful environment for conducting open source web-based reconnaissance quickly and thoroughly.

The interface is modelled on the Metasploit Framework, reducing the learning curve. recon-og is not intended to compete with existing frameworks — it is designed exclusively for web-based open source reconnaissance. If you want to exploit, use Metasploit. If you want to social engineer, use SET. If you want to recon, use recon-og.

recon-og is a completely modular framework. The upstream [Wiki](https://github.com/lanmaster53/recon-ng/wiki) and [Development Guide](https://github.com/lanmaster53/recon-ng/wiki/Development-Guide) are useful references, though some details may differ from this fork. When in doubt, read the source.

## Marketplace

Modules are hosted at [github.com/ncurran/recon-og-marketplace](https://github.com/ncurran/recon-og-marketplace). The marketplace includes new modules not present in upstream recon-ng (Wayback Machine subdomain enumeration, HackerTarget ASN lookup, Cert Spotter certificate transparency), fixes for bugs that are still open upstream (a `whois_miner` "no results" string change that broke the module, HackerTarget quota responses that crash the line parser), and a test suite that runs all modules offline without a live framework install. See the [marketplace README](https://github.com/ncurran/recon-og-marketplace#readme) for the full list.

## Provenance chains

Every entity table (hosts, domains, contacts, credentials, ports, vulnerabilities, etc.) has a `provenance` column that records the chain of modules that produced each row. When a derivative module like `permute` consumes input from `brute_hosts`, which in turn consumed input from `pdcloud_associated`, the resulting host is recorded with `provenance='pdcloud_associated.brute_hosts.permute'` — the full lineage in one column.

The column is hidden from the default `show <table>` view because chains can grow long. To see it, append `all`:

```
[recon-og][acme.com] > show hosts
+----+--------------------------------+----------+--------+---------+--------+
| id | host                           | ip       | region | country | module |
+----+--------------------------------+----------+--------+---------+--------+
| 1  | mail.acme.com                  | 10.0.0.1 |        |         | permute|
+----+--------------------------------+----------+--------+---------+--------+

[recon-og][acme.com] > show hosts all
+----+----------------+----------+--------+---------+---------+----------------------------------+
| id | host           | ip       | region | country | module  | provenance                       |
+----+----------------+----------+--------+---------+---------+----------------------------------+
| 1  | mail.acme.com  | 10.0.0.1 |        |         | permute | alienvault.brute_hosts.permute   |
+----+----------------+----------+--------+---------+---------+----------------------------------+
```

For a focused per-row lookup, the `provenance` command prints just the chain:

```
[recon-og][acme.com] > provenance hosts mail.acme.com
[*]   alienvault.brute_hosts.permute
```

A row's `module` column always records the leaf inserter (the module that wrote that row); `provenance` records how it got there. Modules opt into populating the column via the `'accepts_provenance': True` meta flag — modules that don't opt in leave the column NULL, so existing data and behaviour are unchanged.
