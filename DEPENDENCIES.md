# Dependency Policy

Stock Pilot keeps its orchestration and public-source adapters in this repository. A clean clone can run contract tests, offline analysis, and the public Yahoo/FRED/Cboe/Treasury live path without installing another data skill.

| Skill or tool | Runtime role | Required for live run | Distribution |
|---|---|---:|---|
| `longbridge-content` | SEC filing evidence and news catalysts | Yes | Bundled; manifest declares MIT |
| `longbridge-fundamentals` | Financial reconciliation and valuation | Yes | Bundled; manifest declares MIT |
| `technical_public.py` | Yahoo public technical indicators and price structure | Yes | Bundled Stock Pilot code |
| `macro_public.py` | FRED CSV, Cboe VIX and Treasury macro layer | Yes | Bundled Stock Pilot code |
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

The bundled public entrypoints are:

- `skill/stock-pilot/scripts/technical_public.py`
- `skill/stock-pilot/scripts/macro_public.py`

Run `doctor` before a live run. Optional social and reasoning skills can be added separately. Do not copy a third-party skill into a fork or release until its upstream source and redistribution license have been verified. The absence of a license must be treated as no redistribution permission.
