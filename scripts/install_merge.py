#!/usr/bin/env python3
"""Fusion idempotente de hooks.json dans settings.json + snippet dans CLAUDE.md.

usage : install_merge.py <dossier_repo> <python_cmd> [--uninstall]
Bibliothèque standard uniquement. Fait un backup .bak avant toute modification.
"""
import json
import os
import re
import shutil
import sys

MARK = "skills-auto-use-best-skills"
S, E = "<!-- %s:start -->" % MARK, "<!-- %s:end -->" % MARK


def backup(p):
    if os.path.exists(p):
        shutil.copy2(p, p + ".bak")


def merge_settings(path, hooks, uninstall):
    cfg = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        cfg = json.loads(txt) if txt.strip() else {}
    backup(path)
    cur = cfg.setdefault("hooks", {})
    for event, groups in hooks["hooks"].items():
        kept = []
        for g in cur.get(event, []):  # retire nos anciens hooks (idempotence)
            g["hooks"] = [h for h in g.get("hooks", []) if MARK not in h.get("command", "")]
            if g["hooks"]:
                kept.append(g)
        cur[event] = kept if uninstall else kept + groups
        if not cur[event]:
            del cur[event]
    if not cur:
        del cfg["hooks"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def merge_claude_md(path, snippet, uninstall):
    txt = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
    backup(path)
    txt = re.sub(re.escape(S) + r".*?" + re.escape(E) + r"\n?", "", txt, flags=re.S)
    if not uninstall:
        txt = txt.rstrip("\n") + ("\n\n" if txt.strip() else "") + snippet.strip("\n") + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


def main():
    repo, py = sys.argv[1], sys.argv[2]
    un = "--uninstall" in sys.argv
    home = os.path.join(os.path.expanduser("~"), ".claude")
    os.makedirs(home, exist_ok=True)
    router = os.path.join(home, "skills", MARK, "scripts", "skill_router.py").replace("\\", "/")
    with open(os.path.join(repo, "templates", "hooks.json"), "r", encoding="utf-8") as f:
        hooks = json.loads(f.read().replace("{{PY}}", py).replace("{{ROUTER}}", router))
    with open(os.path.join(repo, "templates", "CLAUDE.md.snippet"), "r", encoding="utf-8") as f:
        snip = f.read()
    merge_settings(os.path.join(home, "settings.json"), hooks, un)
    merge_claude_md(os.path.join(home, "CLAUDE.md"), snip, un)
    print(("désinstallé" if un else "installé") + " : hooks + snippet (backups .bak créés)")


if __name__ == "__main__":
    main()
