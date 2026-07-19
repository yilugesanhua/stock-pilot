# Dependency Policy

Stock Pilot keeps its orchestration and public-source fallback code in this repository. Some data adapters remain separate Codex skills. A clean clone can run the contract tests and render existing data offline; live `collect` and `run` require the adapters marked as required below.

| Skill or tool | Runtime role | Required for live run | Distribution |
|---|---|---:|---|
| `longbridge-content` | SEC filing evidence and news catalysts | Yes | Bundled; manifest declares MIT |
| `longbridge-fundamentals` | Financial reconciliation and valuation | Yes | Bundled; manifest declares MIT |
| `technical-analysis` | Technical indicators and price structure | Yes | Install separately from an authorized source |
| `api-data-fetcher` | FRED, Cboe VIX and Treasury macro layer | Yes | Install separately from an authorized source |
| `agent-reach` | X and Reddit supplemental cross-check | No | Install separately; upstream project: https://github.com/Panniantong/Agent-Reach |
| `us-stock-analysis` | Optional reasoning references | No | Install separately from an authorized source |
| Longbridge CLI | Optional structured data acceleration | No | Proprietary; never redistributed here |

## Expected Paths

The runtime first checks this repository:

```text
dependencies/skills/<skill-name>/scripts/<script-name>
```

It then checks the user's Codex installation:

```text
~/.codex/skills/<skill-name>/scripts/<script-name>
```

The required external entrypoints are:

- `technical-analysis/scripts/technicals.py`
- `api-data-fetcher/scripts/fetch_market_macro.py`

Run `doctor` after installing dependencies. Do not copy a third-party skill into a fork or release until its upstream source and redistribution license have been verified. The absence of a license must be treated as no redistribution permission.
