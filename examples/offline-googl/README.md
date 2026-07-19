# Offline GOOGL Fixture

This directory contains synthetic, non-current data used to test the deterministic `audit -> analyze -> report` pipeline without network access. Values are intentionally illustrative and must not be presented as current market data or investment research.

Run:

```powershell
uv run --project skill/stock-pilot python tools/offline_smoke.py
```
