# PM UpDown Bot Bundle

> **Polymarket Trading Bot** — Shadow-mode autonomous trader with strategy framework, venue connectors, and Ralph automation.

## 📋 Overview

`pm_updown_bot_bundle` is an active trading bot project for [Polymarket](https://polymarket.com/). The bundle contains:

- **Core runner** (`runner.py`) — Main bot loop with shadow-mode support
- **Trading strategies** (`strategies/`) — Modular strategy implementations
- **Venue connectors** (`venues/`) — Polymarket API integrations
- **Automation** (`.ralph/`, `scripts/`) — Ralph configs and shell automation
- **Testing infrastructure** — Truth gate validation suite

**Status:** ACTIVE | **Language:** Python | **Last update:** 2026-07-06

---

## 🏗️ Repository Structure

```
.
├── runner.py                 # Main bot entry point
├── init.sh                   # Environment bootstrap
├── feature_list.json         # Feature tracking (truth gate verification)
├── AGENTS.md                 # Agent operating manual (for AI coding)
│
├── strategies/               # Trading strategy modules
├── venues/                   # Exchange/venue connectors (Polymarket)
├── utils/                    # Shared utilities and helpers
├── scripts/                  # Run scripts, test automation
├── .ralph/                   # Ralph automation configs
├── docs/                     # Strategy docs, architecture notes
├── notes/                    # Research and working notes
├── logs/                     # Runtime logs (gitignored)
└── .env                      # Environment config (secrets managed)
```

---

## 🚀 Quick Start

### 1. Initialize Environment

```bash
git clone https://github.com/GurthBro0ks/pm_updown_bot_bundle.git
cd pm_updown_bot_bundle
source init.sh
```

### 2. Run in Shadow Mode (Safe Testing)

```bash
python runner.py --shadow
```

### 3. Execute Micro Trading Loop

```bash
./run-micro-live.sh
```

### 4. Run Test Suite

```bash
./scripts/run_tests.sh
```

---

## 📊 Features & Status

| ID | Category | Feature | Status |
|----|----------|---------|--------|
| bot-001 | core | Runner starts without errors (shadow mode) | 🔴 |
| bot-002 | core | All strategy modules import cleanly | 🔴 |
| bot-003 | testing | Test suite passes (truth gate green) | 🔴 |
| bot-004 | venue | Polymarket venue connector handles API responses | 🔴 |
| bot-005 | automation | Micro trading loop executes successfully | 🔴 |
| bot-006 | automation | Ralph automation configs valid & parseable | 🔴 |

**Legend:** 🟢 Pass | 🔴 Fail

For detailed feature status, see `feature_list.json`.

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|-----------|
| **Language** | Python 3.x |
| **Trading Venue** | Polymarket |
| **Strategy Pattern** | Module-based (strategies/) |
| **Automation** | Ralph (.ralph/) + Shell scripts |
| **Configuration** | .env (secrets) + JSON/YAML |

---

## 📖 Documentation

- **[AGENTS.md](./AGENTS.md)** — Agent operating manual (startup sequence, truth gate, work rules)
- **[CHANGELOG.md](./CHANGELOG.md)** — Version history
- **[VERSION.md](./VERSION.md)** — Current version info
- **[docs/](./docs/)** — Architecture notes, strategy documentation
- **[notes/](./notes/)** — Research and working notes

---

## 🔐 Safety & Secrets

⚠️ **DO NOT COMMIT:**
- `.env*` files (secrets management)
- Wallet keys, seeds, mnemonics
- Private API keys

These are handled via environment configuration only. See `.gitignore`.

---

## 📝 Workflow Rules

1. **Shadow-mode first** — Always test in shadow mode before live execution
2. **Truth gate validation** — All changes must pass `./scripts/run_tests.sh`
3. **Feature tracking** — Update `feature_list.json` when completing features
4. **One feature per session** — Keep PRs focused and surgical
5. **Minimal diffs** — Prefer small, targeted changes
6. **Documentation** — Update docs/ when adding new strategies

---

## 🔗 Related Projects

- `slimy-monorepo` — Parent monorepo
- `mission-control` — Bot orchestration
- `slimy-kb` — Knowledge base & documentation

---

## 📞 Support & Issues

Open issues for bugs, feature requests, or questions:

- [Issues](https://github.com/GurthBro0ks/pm_updown_bot_bundle/issues)
- [Pull Requests](https://github.com/GurthBro0ks/pm_updown_bot_bundle/pulls)

---

## 📄 License

[Specify or TBD]

---

**Last Updated:** 2026-07-07 | **Maintainer:** [@GurthBro0ks](https://github.com/GurthBro0ks)
