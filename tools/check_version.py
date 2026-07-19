#!/usr/bin/env python3
"""Verify that project metadata and an optional release tag agree."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (ROOT / "pyproject.toml", ROOT / "skill" / "stock-pilot" / "pyproject.toml")


def version(path: Path) -> str:
    with path.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def main() -> int:
    versions = {path.relative_to(ROOT).as_posix(): version(path) for path in FILES}
    unique = set(versions.values())
    if len(unique) != 1:
        print(f"Version mismatch: {versions}", file=sys.stderr)
        return 1

    current = unique.pop()
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag.startswith("v") and tag[1:] != current:
        print(f"Tag {tag} does not match project version {current}", file=sys.stderr)
        return 1

    print(f"Version OK: {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
