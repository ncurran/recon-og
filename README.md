# recon-og

Grandma's favorite recon tool, now featuring AI slop! A fork of the [Recon-ng Framework](https://github.com/lanmaster53/recon-ng) with updated modules and bug fixes; we stand on the shoulders of ~~giants~~ original gangsters.

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
