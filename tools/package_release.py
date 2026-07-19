#!/usr/bin/env python3
"""Build a deterministic release ZIP from tracked repository files."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXCLUDED_PREFIXES = (".github/", ".git/")
EXCLUDED_FILES = {"PUBLISHING_CHECKLIST.md"}


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def tracked_files() -> list[str]:
    command = ["git", "ls-files", "-z"]
    if os.name == "nt":
        # On some Codex Windows setups Git is exposed through git.cmd, which
        # CreateProcess cannot execute directly without cmd.exe.
        command = ["cmd", "/d", "/c", "git ls-files -z"]
    output = subprocess.check_output(
        command, cwd=ROOT
    )
    paths = output.decode("utf-8").split("\0")
    return sorted(
        path
        for path in paths
        if path
        and path not in EXCLUDED_FILES
        and not path.startswith(EXCLUDED_PREFIXES)
    )


def main() -> int:
    version = project_version()
    DIST.mkdir(exist_ok=True)
    archive = DIST / f"stock-pilot-v{version}.zip"
    root_name = f"stock-pilot-v{version}"

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in tracked_files():
            source = ROOT / relative
            info = zipfile.ZipInfo(f"{root_name}/{relative}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            executable = relative.endswith(".sh")
            info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, source.read_bytes())

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksums = DIST / "checksums.txt"
    checksums.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Built {archive.relative_to(ROOT)} ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
