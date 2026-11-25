#!/usr/bin/env python3
"""skills-auto-use-best-skills : routeur de skills silencieux et ultra-léger.

Bibliothèque standard uniquement. Deux usages :
  - hooks Claude Code : `skill_router.py hook sessionstart|prompt|precompact` (JSON sur stdin)
  - commandes manuelles : `skill_router.py diag|pin <s>|unpin <s>|lock <s>|unlock <s>|reset|sync`

Principe : on n'indexe QUE le frontmatter des SKILL.md (jamais le corps), on ne met dans le
contexte qu'un micro-bloc clé=valeur, et seulement quand quelque chose change (delta).
"""
import json
import math
import os
import re
import sys
import time

SELF = "skills-auto-use-best-skills"
STICKY_SECS = 600          # un skill choisi reste "en cours" 10 min
PRUNE_SECS = 86400         # les skills auto inactifs sont oubliés après 24 h
MAX_AUTO = 2               # skills métier max par tour (3 avec graphify)
MIN_SCORE = 3.0
IDX_NAMES_CAP = 40
STOP = set("""the and for with that this from into your you are not but can use using when what how why will have has
one any all also than then them they their our out per via est les des une pour dans avec sur par que qui quoi
pas plus mais ont sont aux ses mon ton son nos vos leur leurs ces cet cette fait faire peux veux comme
skill skills claude code user users should must may need needs make makes build builds create creates
""".split())
ARCH_RE = re.compile(r"architecture|dependenc|dépendanc|call graph|codebase|base de code|structure du projet|how does .* work|comment .* fonctionne", re.I)
CONT_RE = re.compile(r"^\s*(ok|oui|yes|go|continue|continues|suite|vas-y|vas y|next|et ensuite|merci|d'accord|parfait)\b[\s\S]{0,40}$", re.I)


def _out(s):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stdout.write(s)


def home_claude():
    return os.path.join(os.path.expanduser("~"), ".claude")


def project_claude(cwd):
    p = os.path.join(cwd, ".claude")
    return p if os.path.isdir(p) else None


def data_dir(cwd):
    """Dossier des fichiers d'état/index : projet si .claude existe, sinon ~/.claude."""
    return project_claude(cwd) or home_claude()


# ---------- frontmatter ----------
def parse_frontmatter(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(6000)  # frontmatter seulement, jamais le corps entier
    except OSError:
        return None
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---", head, re.S)
    if not m:
        return None
    lines = m.group(1).splitlines()
    out, i = {}, 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", lines[i])
        if not km:
            i += 1
            continue
        key, val = km.group(1), km.group(2).strip()
        if val in (">", "|", ">-", "|-"):
            buf = []
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            val = " ".join(b for b in buf if b)
        else:
            i += 1
            while i < len(lines) and lines[i].startswith("  ") and not re.match(r"^\s+[\w-]+\s*:", lines[i]):
                val += " " + lines[i].strip()
                i += 1
        val = val.strip().strip("\"'")
        out[key] = val
    return out


def tokens(text):
    res = []
    for w in re.findall(r"[a-zà-ÿ0-9][a-zà-ÿ0-9_\-]{2,}", text.lower()):
        w = w.strip("-_")
        if len(w) < 3 or w in STOP:
            continue
        if len(w) > 4 and w[-1] in "sx":
            w = w[:-1]
        res.append(w)
    return res


def condense(desc, limit=200):
    desc = re.sub(r"\s+", " ", desc).strip()
    sents = re.split(r"(?<=[.!?])\s+", desc)
    return " ".join(sents[:2])[:limit]


def build_entry(path, fm, mtime):
    name = fm.get("name") or os.path.basename(os.path.dirname(path))
    desc = fm.get("description", "")
    extra = fm.get("triggers", "") + " " + fm.get("keywords", "")
    freq = {}
    for t in tokens(desc) + tokens(extra) * 2 + tokens(name.replace("-", " ")) * 3:
        freq[t] = freq.get(t, 0) + 1
    kws = [k for k, _ in sorted(freq.items(), key=lambda kv: -kv[1])][:14]
    return {"name": name, "path": path, "mtime": mtime, "desc": condense(desc), "kw": kws}


# ---------- index ----------
def skill_files(cwd):
    roots = [os.path.join(home_claude(), "skills"), os.path.join(cwd, ".claude", "skills")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            depth = dp[len(root):].count(os.sep)
            if depth > 3:
                dn[:] = []
                continue
            if "SKILL.md" in fn:
                yield os.path.join(dp, "SKILL.md")


def sync_index(cwd):
    ddir = data_dir(cwd)
    ipath = os.path.join(ddir, "skills-index.json")
    old = read_json(ipath, {"entries": {}})
    entries, changed = {}, False
    for p in skill_files(cwd):
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        prev = old.get("entries", {}).get(p)
        if prev and prev.get("mtime") == mt:
            entries[p] = prev
            continue
        fm = parse_frontmatter(p)
        if not fm:
            continue
        e = build_entry(p, fm, mt)
        if e["name"] == SELF:
            continue
        entries[p] = e
        changed = True
    if set(entries) != set(old.get("entries", {})):
        changed = True
    idx = {"version": 1, "entries": entries}
    if changed:
        write_json(ipath, idx)
    return idx


def load_index(cwd):
    """Lecture seule du cache (chemin rapide des prompts) ; resync complète si absent."""
    idx = read_json(os.path.join(data_dir(cwd), "skills-index.json"), None)
    return idx if isinstance(idx, dict) and "entries" in idx else sync_index(cwd)


# ---------- état ----------
def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)  # écriture atomique


def state_path(cwd):
    p = project_claude(cwd)
    return os.path.join(p, "active-skills-state.json") if p else os.path.join(home_claude(), "active-skills-state.json")


def fresh_state():
    return {"version": 1, "router_always_on": True, "context_epoch": 0, "graphify_active": False,
            "active_skills": {}, "last_sig": "", "last_inject_chars": 0, "reinject_pending": False,
            "updated": 0}


def load_state(cwd):
    s = read_json(state_path(cwd), None)
    if not isinstance(s, dict):
        s = read_json(os.path.join(home_claude(), "active-skills-state.json"), None)
    base = fresh_state()
    if isinstance(s, dict):
        base.update(s)
    base["router_always_on"] = True
    return base


def save_state(cwd, st):
    st["updated"] = int(time.time())
    write_json(state_path(cwd), st)


def prune(st, now):
    act = st["active_skills"]
    for n, a in list(act.items()):
        if a["status"] in ("pinned",) or a.get("locked"):
            continue
        age = now - a.get("ts", 0)
        if age > PRUNE_SECS:
            del act[n]
        elif a["status"] == "in_progress" and age > STICKY_SECS:
            a["status"] = "auto"


def graphify_on(cwd):
    return os.path.isdir(os.path.join(cwd, "graphify-out"))


# ---------- routage ----------
def route(prompt, idx, cwd):
    entries = list(idx["entries"].values())
    if not entries:
        return [], False
    names = {e["name"]: e for e in entries}
    qt = set(tokens(prompt))
    pl = prompt.lower()
    df = {}
    for e in entries:
        for k in set(e["kw"]):
            df[k] = df.get(k, 0) + 1
    n = len(entries)
    scored = []
    for e in entries:
        if e["name"] == "graphify":
            continue  # composé séparément
        s = sum(math.log(1 + n / df[k]) for k in e["kw"] if k in qt)
        if re.search(r"(?<![\w-])" + re.escape(e["name"].lower()) + r"(?![\w-])", pl):
            s += 6
        if s >= MIN_SCORE:
            scored.append((s, e["name"]))
    scored.sort(reverse=True)
    picked = []
    if scored:
        top = scored[0][0]
        picked = [nm for s, nm in scored[:MAX_AUTO] if s >= 0.6 * top]
    g = "graphify" in names and (graphify_on(cwd) or "graphify" in pl or bool(ARCH_RE.search(prompt)))
    return picked, bool(g)


def upsert(st, name, status, now, locked=None):
    a = st["active_skills"].setdefault(name, {"status": status, "ts": now, "loaded_epoch": -1})
    if a["status"] != "pinned":
        a["status"] = status
    a["ts"] = now
    if locked is not None:
        a["locked"] = locked
    return a


def evict(st, protect):
    """Plafond de 3 skills non épinglés/non verrouillés ; on évince les plus anciens."""
    free = [(a["ts"], n) for n, a in st["active_skills"].items()
            if a["status"] != "pinned" and not a.get("locked") and n not in protect]
    free.sort()
    over = len(free) - 3
    for _, n in free[:max(0, over)]:
        del st["active_skills"][n]


def active_names(st, now):
    return [n for n, a in st["active_skills"].items()
            if a["status"] in ("pinned", "in_progress") or a.get("locked")]


def sig_of(names, graphify):
    order = ["graphify"] * graphify + [n for n in names if n != "graphify"]
    return "[skills: %s]" % " + ".join(order) if order else ""


def micro(lines):
    return "\n".join(l for l in lines if l)


def block_for(st, names, idx, epoch_start):
    names = [n for n in names]
    pins = [n for n, a in st["active_skills"].items() if a["status"] == "pinned"]
    paths = {e["name"]: e["path"] for e in idx["entries"].values()}
    lines = ["router epoch=%d" % st["context_epoch"]]
    if names:
        lines.append("use=" + ",".join(names))
        lines.append("load=" + ";".join(paths[n] for n in names if n in paths))
    if pins and epoch_start:
        lines.append("pinned=" + ",".join(pins))
    if st.get("graphify_active"):
        lines.append("graphify=on (keep graphify-out/ as structural context; additive)")
    if epoch_start:
        nm = sorted(e["name"] for e in idx["entries"].values())[:IDX_NAMES_CAP]
        lines.append("idx=" + ",".join(nm))
    sig = sig_of(names, st.get("graphify_active") and "graphify" in names)
    if sig and sig != st.get("last_sig"):
        lines.append("sig=" + sig + " (print once at start of reply)")
    lines.append("do=invoke listed skills via Skill tool silently; never ask confirmation; never interrupt a running task")
    return micro(lines)


# ---------- hooks ----------
def read_stdin():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def hook(kind):
    data = read_stdin()
    cwd = data.get("cwd") or os.getcwd()
    now = time.time()
    st = load_state(cwd)
    if kind == "precompact":
        st["context_epoch"] += 1
        st["reinject_pending"] = True
        save_state(cwd, st)
        return
    idx = sync_index(cwd) if kind == "sessionstart" else load_index(cwd)
    st["graphify_active"] = graphify_on(cwd)
    prune(st, now)
    if kind == "sessionstart":
        src = data.get("source", "startup")
        if not (src == "compact" and st.get("reinject_pending")):
            st["context_epoch"] += 1
        st["reinject_pending"] = False
        names = active_names(st, now)
        gl = st["graphify_active"] and "graphify" in {e["name"] for e in idx["entries"].values()}
        if gl and "graphify" not in names:
            names.append("graphify")
        blk = block_for(st, names, idx, True)
        st["last_sig"] = sig_of(names, gl)
        for n in names:
            if n in st["active_skills"]:
                st["active_skills"][n]["loaded_epoch"] = st["context_epoch"]
        st["last_inject_chars"] = len(blk)
        save_state(cwd, st)
        _out(blk + "\n")
        return
    # UserPromptSubmit
    prompt = data.get("prompt", "") or ""
    if prompt.lstrip().lower().startswith("/" + SELF):
        return  # anti-récursion
    m = re.match(r"^\s*/([\w:-]+)", prompt)
    if m and any(e["name"] == m.group(1) for e in idx["entries"].values()):
        upsert(st, m.group(1), "in_progress", now)  # invocation explicite : rien à injecter
        st["active_skills"][m.group(1)]["loaded_epoch"] = st["context_epoch"]
        save_state(cwd, st)
        return
    cont = bool(CONT_RE.match(prompt)) or len(prompt.split()) <= 2
    picked, g = ([], False) if cont else route(prompt, idx, cwd)
    for n in picked:
        upsert(st, n, "in_progress", now)
    if g:
        upsert(st, "graphify", "in_progress", now)
    if cont:  # tâche en cours : on prolonge le verrou sticky sans rien réinjecter
        for n, a in st["active_skills"].items():
            if a["status"] == "in_progress":
                a["ts"] = now
    evict(st, set(picked) | ({"graphify"} if g else set()))
    to_load = [n for n in picked + (["graphify"] if g else [])
               if st["active_skills"][n].get("loaded_epoch", -1) != st["context_epoch"]]
    if st.get("reinject_pending"):
        to_load = list(dict.fromkeys(active_names(st, now) + to_load))
    out = ""
    if to_load or st.get("reinject_pending"):
        out = block_for(st, to_load, idx, bool(st.get("reinject_pending")))
        for n in to_load:
            st["active_skills"][n]["loaded_epoch"] = st["context_epoch"]
        shown = active_names(st, now)
        st["last_sig"] = sig_of(shown, "graphify" in shown)
        st["reinject_pending"] = False
    st["last_inject_chars"] = len(out)
    save_state(cwd, st)
    if out:
        _out(out + "\n")


# ---------- commandes manuelles ----------
def diag(cwd):
    idx = sync_index(cwd)
    st = load_state(cwd)
    now = time.time()
    prune(st, now)
    act = st["active_skills"]
    idx_chars = os.path.getsize(os.path.join(data_dir(cwd), "skills-index.json")) if os.path.exists(os.path.join(data_dir(cwd), "skills-index.json")) else 0
    locked = [n for n, a in act.items() if a.get("locked")]
    _out("\n".join([
        "skills-auto-use-best-skills · diagnostic",
        "indexed=%d  epoch=%d  always_on=true" % (len(idx["entries"]), st["context_epoch"]),
        "active=" + (", ".join("%s(%s%s)" % (n, a["status"], ",locked" if a.get("locked") else "") for n, a in act.items()) or "none"),
        "graphify=%s (graphify-out/ %s)" % ("on" if graphify_on(cwd) else "off", "present" if graphify_on(cwd) else "absent"),
        "task_lock=%s" % (",".join(locked) if locked else "none (sticky %ds sur skills en cours)" % STICKY_SECS),
        "tokens≈ last_inject=%d, index_on_disk(not in context)=%d, per-turn overhead≈0-80" % (st["last_inject_chars"] // 4, idx_chars // 4),
        "state=" + state_path(cwd),
    ]) + "\n")


def cmd(argv):
    cwd = os.getcwd()
    c = argv[0] if argv else "diag"
    now = time.time()
    if c in ("pin", "unpin", "lock", "unlock") and len(argv) < 2:
        _out("usage: %s <skill>\n" % c)
        return 2
    st = load_state(cwd)
    if c == "diag":
        return diag(cwd)
    if c == "sync":
        idx = sync_index(cwd)
        _out("indexed=%d\n" % len(idx["entries"]))
        return 0
    if c == "reset":
        keep = st["context_epoch"]
        st = fresh_state()
        st["context_epoch"] = keep + 1
        st["reinject_pending"] = False
        save_state(cwd, st)
        _out("state reset\n")
        return 0
    name = argv[1]
    names = {e["name"] for e in sync_index(cwd)["entries"].values()}
    if c in ("pin", "lock") and name not in names:
        _out("unknown skill: %s\n" % name)
        return 1
    if c == "pin":
        upsert(st, name, "pinned", now)
    elif c == "unpin":
        a = st["active_skills"].get(name)
        if a and a["status"] == "pinned":
            a["status"] = "auto"
    elif c == "lock":
        upsert(st, name, "in_progress", now, locked=True)
    elif c == "unlock":
        a = st["active_skills"].get(name)
        if a:
            a["locked"] = False
    else:
        _out("commands: diag | pin <s> | unpin <s> | lock <s> | unlock <s> | reset | sync\n")
        return 2
    save_state(cwd, st)
    _out("%s %s ok\n" % (c, name))
    return 0


def main():
    a = sys.argv[1:]
    if a and a[0] == "hook" and len(a) > 1:
        try:
            hook(a[1])
        except Exception:
            pass  # un hook ne doit jamais bloquer le prompt
        return 0
    return cmd(a)


if __name__ == "__main__":
    sys.exit(main())
