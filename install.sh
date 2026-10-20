#!/usr/bin/env bash
# qa-kit installer — user scope only. Safe to run repeatedly.
#
#   bash install.sh [--no-clone] [--kb PATH] [--dry-run]
#
#   --no-clone   do not clone the knowledge repo if it is missing (QA_KB is still set)
#   --kb PATH    knowledge repo location (default: $QA_KB or ~/Workspace/qa-knowledge)
#   --dry-run    print what would change
#
# It links skills into ~/.claude/skills and ~/.agents/skills, agents into
# ~/.claude/agents, bin/qa into ~/.local/bin/qa, and sets env.QA_KB in
# ~/.claude/settings.json (backup first, other keys preserved).
# It never touches project repositories.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
KB_SLUG="dockylitmers/qa-knowledge"
CLONE=1
DRY=0
KB="${QA_KB:-$HOME/Workspace/qa-knowledge}"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-clone) CLONE=0 ;;
    --kb) KB="$2"; shift ;;
    --kb=*) KB="${1#--kb=}" ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
case "$KB" in "~"*) KB="$HOME${KB#\~}" ;; esac

say() { printf '  %s\n' "$*"; }
do_run() { if [ "$DRY" = 1 ]; then say "(dry-run) $*"; else "$@"; fi; }

# link SRC DST — create or repair a symlink; refuse to clobber a real file/dir.
link() {
  local src="$1" dst="$2"
  if [ -L "$dst" ]; then
    if [ "$(readlink "$dst")" = "$src" ]; then say "ok      $dst"; return; fi
    do_run rm "$dst"
  elif [ -e "$dst" ]; then
    echo "install.sh: $dst exists and is not a symlink; move it away and re-run" >&2
    exit 1
  fi
  do_run ln -s "$src" "$dst"
  say "linked  $dst -> $src"
}

echo "qa-kit: installing from $KIT_DIR"

for base in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
  do_run mkdir -p "$base"
  for s in "$KIT_DIR"/skills/*/; do
    s="${s%/}"
    link "$s" "$base/$(basename "$s")"
  done
done

do_run mkdir -p "$HOME/.claude/agents"
for a in "$KIT_DIR"/agents/*.md; do
  link "$a" "$HOME/.claude/agents/$(basename "$a")"
done

chmod +x "$KIT_DIR/bin/qa"
do_run mkdir -p "$HOME/.local/bin"
link "$KIT_DIR/bin/qa" "$HOME/.local/bin/qa"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) say "NOTE    ~/.local/bin is not on PATH; add to your shell profile:"
     say "        export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# Merge env.QA_KB into ~/.claude/settings.json, keeping every other key.
SETTINGS="$HOME/.claude/settings.json"
if [ "$DRY" = 1 ]; then
  say "(dry-run) set env.QA_KB=$KB in $SETTINGS"
else
  python3 - "$SETTINGS" "$KB" <<'PY'
import json, os, shutil, sys, time
path, kb = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        sys.exit(f"install.sh: {path} is not valid JSON; fix it and re-run")
    if not isinstance(data, dict):
        sys.exit(f"install.sh: {path} is not a JSON object")
    env = data.get("env", {})
    if not isinstance(env, dict):
        sys.exit(f"install.sh: {path} has a non-object 'env'; fix it and re-run")
    if env.get("QA_KB") == kb:
        print(f"  ok      {path} env.QA_KB")
        sys.exit(0)
    backup = f"{path}.bak-qa-kit-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    print(f"  backup  {backup}")
else:
    os.makedirs(os.path.dirname(path), exist_ok=True)
data.setdefault("env", {})["QA_KB"] = kb
tmp = path + ".tmp-qa-kit"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp, path)
print(f"  set     {path} env.QA_KB={kb}")
PY
fi

if [ -d "$KB/.git" ]; then
  say "ok      knowledge repo at $KB"
elif [ "$CLONE" = 1 ]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "install.sh: gh not found; install it and run 'gh auth login', or pass --no-clone" >&2
    exit 1
  fi
  do_run mkdir -p "$(dirname "$KB")"
  do_run gh repo clone "$KB_SLUG" "$KB"
  say "cloned  $KB_SLUG -> $KB"
else
  say "skip    knowledge repo clone (--no-clone); expected at $KB"
fi

echo "qa-kit: done. Open a new Claude Code session so QA_KB is picked up, then run: qa doctor"
