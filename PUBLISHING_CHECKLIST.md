# Publishing Checklist

- [ ] Replace the placeholder `SEC_USER_AGENT` only in local environment state.
- [ ] Run a secret scanner over the working tree and Git history.
- [ ] Confirm no screenshots, runs, caches, cookies, tokens, or binaries are tracked.
- [ ] Verify every dependency license and retain its notice.
- [ ] Install external Codex skills from authorized upstream sources.
- [ ] Run the offline CI tests from a clean clone.
- [ ] Run `doctor` with live credentials only outside CI.
- [ ] Review data-provider terms, rate limits, and redistribution restrictions.
- [ ] Review financial-research disclaimers for the jurisdictions where the repository will be published.
- [ ] Confirm both `pyproject.toml` files match the release tag.
- [ ] Confirm CI and CodeQL pass on the release commit.
- [ ] Update `CHANGELOG.md` before publishing the release.
