---
name: stock-pilot
description: >-
  Analyze a US-listed stock or ADR for a default 2-8 week holding period using
  current SEC filings, reconciled fundamentals, public Yahoo technicals, Cboe VIX,
  official US macro data, recent news, and X/Reddit cross-checks. Use when the
  user enters a US ticker or asks for a direct buy, wait, avoid, hold, reduce,
  or sell view. Produces Chinese Markdown and JSON, never trades or sizes positions.
---

# Stock Pilot

## Overview

Run the complete read-only US-stock workflow. Default horizon is 2-8 weeks. Core
data must prove it is current before any action recommendation is allowed.

## Dependencies

- `longbridge-fundamentals`: company, valuation, and structured financial data; Longbridge is optional.
- `longbridge-content`: filings, corporate actions, and recent news; SEC/Google News public fallback is automatic.
- `technical-analysis`: deterministic indicators from public Yahoo daily candles when Longbridge is unavailable.
- `api-data-fetcher`: FRED history plus current Cboe VIX and US Treasury overlay.
- `us-stock-analysis`: report reasoning framework only; it may not override facts.
- `agent-reach`: X and Reddit cross-checks; social posts are clues, not facts.

The workflow does not require a real brokerage account or Longbridge authorization.
SEC EDGAR, Yahoo Chart, Google News RSS, FRED, Cboe, and the US Treasury are the
default public-source chain. Longbridge only accelerates or enriches data when a
usable session exists; authentication failures must never block a normal run.

Do not invoke `TradingAgents` or `finance-sentiment` as part of this workflow.

## Quick Start

For a natural-language NVDA analysis request, run:

```powershell
uv run --project "$HOME\.codex\skills\stock-pilot" python "$HOME\.codex\skills\stock-pilot\scripts\stock_pilot.py" run --ticker NVDA --horizon 2-8w --output "$HOME\.stock-pilot\runs\NVDA\latest"
```

Read `report.md`, `report.json`, and `quality.json`, then answer in Chinese. State
the direct recommendation first. Every run renders the full standard report contract,
including Bull/Base/Bear, freshness, and sources. `BLOCK_ACTION` runs keep every
section but show `N/A` for prohibited execution levels. Never add position size or
execute a trade.

## Fast Path

- `run` already performs the Agent Reach backend check and one X/Reddit search
  through the news layer. Do not run `agent-reach doctor`, X search, or Reddit
  search again when `news-catalysts.json.agent_reach.verified` is true.
- Read `report.md`, `report.json`, and `quality.json` first. Do not print or load
  entire `filings.json`, `technicals.json`, or `news-catalysts.json` into the
  conversation unless the report omits evidence required by the user.
- When a freshness gate fails, inspect only the conflicting layer and fields.
  For an official/structured period mismatch, extract the selected filing's
  earnings exhibit instead of scanning every filing.
- Technical candles are reused within a run. Public Yahoo Chart URLs are included
  in the report sources. Google News RSS supplies headline metadata without
  downloading article bodies. FRED series summaries use the documented 24-hour
  cache; current Cboe VIX and US Treasury yields are still fetched on every run.
- The standard run uses current news headline metadata (timestamp, source, title,
  URL) and does not download article bodies because the deterministic decision
  engine does not consume them. Inspect a specific article only when its headline
  materially changes the thesis or the user asks for source-level verification.

## Utility Scripts

- `doctor --output doctor.json`: quickly verify runtime, keys, dependencies, and the SEC/Yahoo/Google News public chain. Add `--with-supplemental` only when live X/Reddit smoke tests are needed.
- `collect --ticker TICKER --horizon 2-8w --output DIR`: write five source layers and freshness audit.
- `analyze --input DIR --output DIR`: create a gated deterministic decision record.
- `report --input DIR --output DIR`: render Chinese Markdown from existing JSON without network access.
- `run --ticker TICKER --horizon 2-8w --output DIR`: run collect, analyze, and report.

Every subcommand writes files. Stdout only reports status and paths.

## Decision Contract

- Use technical signals, current Cboe VIX, and first-target risk/reward to determine the 2-8 week action.
- Treat SEC/official financials, current quotes, macro freshness, and recent news as hard quality gates.
- Use fundamentals, valuation availability, and verified events as risk modifiers, not equal-weight action scores.
- Keep X and Reddit supplemental; never let social content enter the core score or replace a fact source.
- For a recent IPO, label an S-1/A offer price as expected unless a final pricing source exists. Never infer listing or lockup dates.

## Rate Limiting

SEC requests use a cross-process lock at 5 requests/second, five-minute cache,
and exponential retry. FRED uses one request/second, a 24-hour series cache, and
one macro snapshot per run. X and Reddit each receive one search per ticker per run.

## Common Mistakes

- Do not call FRED `VIXCLS` the current VIX; current VIX must come from Cboe.
- Do not treat every 6-K as earnings; only content-verified financial 6-K files qualify.
- Do not use GAAP PE alone when `valuation_conclusion_allowed` is false.
- Do not issue an action when `quality.json` says `BLOCK_ACTION`.
