#!/usr/bin/env bash
# Installateur idempotent : copie le skill, fusionne les hooks, ajoute le snippet CLAUDE.md.
# usage : ./install.sh [--uninstall]
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude/skills/skills-auto-use-best-skills"

PY=""
for c in python3 python "py -3"; do
  if $c -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3 introuvable" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  $PY "$REPO/scripts/install_merge.py" "$REPO" "$PY" --uninstall
  rm -rf "$DEST"
  echo "skill supprimé : $DEST"
  exit 0
fi

mkdir -p "$DEST/scripts"
cp "$REPO/SKILL.md" "$DEST/SKILL.md"
cp "$REPO/scripts/skill_router.py" "$DEST/scripts/skill_router.py"
$PY "$REPO/scripts/install_merge.py" "$REPO" "$PY"
$PY "$DEST/scripts/skill_router.py" sync
echo "OK. Relance Claude Code (ou ouvre une nouvelle session) puis tape /skills-auto-use-best-skills"
