# Stock Pilot

`stock-pilot` is a read-only Codex skill for current-data analysis of US-listed stocks and ADRs over a default 2-8 week horizon. It produces Chinese Markdown and JSON reports, applies freshness gates, and never places trades or sizes positions.

## What It Uses

- SEC submissions and Company Facts for official filings
- Yahoo Chart public candles as the technical fallback
- Cboe delayed VIX and US Treasury daily yields
- FRED macro history when configured
- Google News RSS metadata
- Optional Longbridge and Agent Reach integrations

If a core source cannot prove freshness, the report is `BLOCK_ACTION` and execution levels are suppressed.

## Prerequisites

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- A valid `SEC_USER_AGENT` containing a real maintainer email or project URL
- `FRED_API_KEY` for the full FRED path; public CSV fallback remains available for some series
- The external Codex dependency skills listed in `THIRD_PARTY_NOTICES.md`
- Optional: Longbridge CLI, Longbridge login, Agent Reach, and OpenCLI

The repository does not redistribute the Longbridge executable or unlicensed third-party skill source. Install those dependencies separately and run `doctor` before live collection.

## Quick Start

```powershell
Copy-Item .env.example .env
uv sync --project skill/stock-pilot
$env:SEC_USER_AGENT = "stock-pilot/1.0 maintainer@example.com"
uv run --project skill/stock-pilot python skill/stock-pilot/scripts/stock_pilot.py doctor --output doctor.json
uv run --project skill/stock-pilot python skill/stock-pilot/scripts/stock_pilot.py run --ticker GOOGL --horizon 2-8w --output runs/GOOGL/latest
```

The Codex skill entrypoint is `skill/stock-pilot/SKILL.md`. The runtime first looks for bundled, licensed dependency directories and then falls back to `~/.codex/skills`.

## Tests

```powershell
uv run --project skill/stock-pilot python -m unittest discover -s skill/stock-pilot/tests
```

Live market data is not used in CI. Use `doctor` and a real ticker locally for an end-to-end smoke test.

## Data and Legal Notes

This is research software, not investment advice. Market data may be delayed or unavailable. Respect SEC, Yahoo, Google News, Cboe, FRED, Longbridge, and Agent Reach terms and rate limits. Do not commit `.env`, tokens, cookies, generated runs, screenshots, or proprietary binaries.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [SECURITY.md](SECURITY.md), and [PUBLISHING_CHECKLIST.md](PUBLISHING_CHECKLIST.md) before creating a public release.
