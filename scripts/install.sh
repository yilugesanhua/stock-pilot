#!/usr/bin/env sh
set -eu

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SKILLS_ROOT="$CODEX_HOME/skills"

mkdir -p "$SKILLS_ROOT"
rm -rf "$SKILLS_ROOT/stock-pilot"
cp -R "$REPO_ROOT/skill/stock-pilot" "$SKILLS_ROOT/stock-pilot"

for name in longbridge-content longbridge-fundamentals; do
  if [ -d "$REPO_ROOT/dependencies/skills/$name" ]; then
    rm -rf "$SKILLS_ROOT/$name"
    cp -R "$REPO_ROOT/dependencies/skills/$name" "$SKILLS_ROOT/$name"
  fi
done

uv sync --frozen --project "$SKILLS_ROOT/stock-pilot"
printf 'Installed stock-pilot at %s\n' "$SKILLS_ROOT/stock-pilot"
printf 'The public Yahoo/FRED/Cboe/Treasury path is bundled; run doctor to inspect optional integrations.\n'
