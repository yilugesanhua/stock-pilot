# Contributing

Thanks for improving Stock Pilot. Keep changes read-only, reproducible and evidence-backed.

## Development Setup

```powershell
git clone https://github.com/yilugesanhua/stock-pilot.git
cd stock-pilot
uv sync --frozen --project skill/stock-pilot
uv run --project skill/stock-pilot python -m unittest discover -s skill/stock-pilot/tests -v
```

## Pull Requests

1. Open an issue before making a large behavioral or provider change.
2. Keep provider data, credentials, generated reports and screenshots out of Git.
3. Add or update tests for behavior changes.
4. Preserve `BLOCK_ACTION` behavior when freshness cannot be proved.
5. Run the version check, compile check and contract tests locally.
6. Explain provider terms, rate limits and license implications for new integrations.

Do not add automatic trading, account access or position sizing. Do not redistribute third-party code without a verifiable license.

## Commit Style

Use a short imperative subject, for example `Fix SEC filing period selection`. Keep unrelated changes in separate commits.
