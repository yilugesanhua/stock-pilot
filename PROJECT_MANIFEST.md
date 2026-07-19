# Stock Pilot Project Manifest

This folder contains the source and validation harness for the `stock-pilot` Codex skill. It is prepared for public GitHub publication and excludes credentials, private runtime state, and proprietary binaries.

## Included

- `design/data-freshness-policy.md`: public data freshness and gating rules.
- `dependencies/skills/longbridge-fundamentals`: fundamentals and valuation skill source.
- `dependencies/skills/longbridge-content`: SEC filings and news skill source.
- External technical, macro, synthesis, and social skills are installed separately; their copied local sources are ignored until redistribution licenses are verified.
- Longbridge CLI is an external proprietary prerequisite and is not redistributed here.
- Authentication screenshots are intentionally excluded from this public copy.
- `skill/stock-pilot`: maintainable source for the globally installed personal skill.

## Runtime State Not Copied

The following remain installed on this machine and are intentionally excluded:

- Python `.venv` directories, caches, and generated runs.
- Longbridge OAuth tokens.
- `SEC_USER_AGENT`, `FRED_API_KEY`, and any `.env` file.
- Browser cookies and Agent Reach login state.
- Generated stock-analysis runs.

Never commit credentials. Recreate the runtime with `uv sync --frozen --project skill/stock-pilot`.

## Validation Scope

- The public copy's contract test suite runs without live market-data access.
- `doctor` reports missing credentials and optional integrations without crashing.
- Live-provider availability is environment-specific and is not guaranteed by this repository.

## Repaired Runtime State

- Project `pyproject.toml` and `uv.lock` now recreate the Python 3.12 runtime with `uv sync`.
- `pandasdmx` is intentionally excluded from the stock runtime because it requires Pydantic 1 while `trading-skills` requires Pydantic 2; OECD work must use an isolated environment.
- Foreign private issuers use `20-F/6-K` quality-gate equivalents.
- Filing evidence is filtered by core form before result limiting, preventing Form 4 crowd-out.
- Official earnings exhibits override lagging structured periods, and TTM PE is recomputed from the latest four USD EPS observations.
- Technical output includes SMA200, support/resistance, period returns, and benchmark-relative strength.
- News detail and Agent Reach failures are non-blocking; catalysts are marked `confirmed` or `estimated`.
- Current VIX comes from the Cboe delayed quote; FRED VIXCLS is retained only as history and marked stale when it lags.
- Current 2Y/10Y Treasury yields come from the latest US Treasury daily yield-curve release.
- SEC `submissions` and Company Facts provide current filing discovery and domestic-issuer XBRL extraction.
- `tools/freshness_audit.py` blocks action recommendations whenever a core layer cannot prove it is current.

## Skill State

- Install with `scripts/install.ps1`, or copy `skill/stock-pilot` to the Codex skills directory.
- Linux and macOS users can run `scripts/install.sh`.
- Commands: `doctor`, `collect`, `analyze`, `report`, and `run`.
- Historical local validation artifacts are intentionally excluded from the public repository.
- Users must supply their own `SEC_USER_AGENT`, optional API keys, and authorized integrations.
