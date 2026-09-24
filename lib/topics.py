#!/usr/bin/env python3
"""Topic view: every running and recent job on Sandy, grouped by what it is about.

Measured, not maintained (same principle as collect.py):
  * jobs          ~/jobs/<name>-<date>/ (PROMPT.md title, OUTPUT.md, RESULT.md)
  * running state the systemd user units and the process table (cgroup + cwd)
  * engine        the agent binary in the job's process tree, else the job's log files
  * parent        explicit marker > process ancestry > launch command found in another
                  job's / Hermes' logs > default "Claude Code on the laptop" (its SSH
                  commands leave no log on Sandy), always with the evidence attached
  * board         ~/.agent-board/board.db (read-only)
  * cost          ~/.local/state/gpu-pods (monitor state + central ledger)
  * needs Florian ~/.notify/human-todo.json if present, else current "Needs you" notify
                  messages, filtered against DECISIONS.md

Everything expensive is cached; parent lookups are cached forever per job.
"""
import json, os, re, sqlite3, subprocess, threading, time
from pathlib import Path

HOME = Path("/home/flori")
JOBS = HOME / "jobs"
UID = os.getuid()
BOARD_DB = HOME / ".agent-board/board.db"
BOARD_URL = "https://agent-board.app.mintapis.com"
GPU = HOME / ".local/state/gpu-pods"
NOTIFY = HOME / ".notify"
DECISIONS = HOME / "DECISIONS.md"
RECENT_S = 3 * 86400
SENSITIVE = re.compile(r"(api[_-]?key|token|secret|password|passwd|bearer|authorization)(\s*[:=]\s*|\s+)[\"']?[A-Za-z0-9_\-./+=]{8,}|\b(?:napi|rnd|sk|ghp|gho|xox[bp]|hf)_[A-Za-z0-9_\-]{10,}", re.I)

ENV = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{UID}",
           DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{UID}/bus")

def sh(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV).stdout
    except Exception:
        return ""

# ───────────────────────────── topics ─────────────────────────────
# (key, title, name patterns, board thread slugs, standing-automation patterns, owner)
TOPICS = [
    ("imagejevbench", "ImageJevBench", r"image-?jev", ("imagejevbench",), r"image-?jev",
     "ImageJevBench jobs"),
    ("training", "Training · JevK5 / djev", r"jevk5|djev|nvfp4|retrain|lora|finetun|posttrain|eikos|serving-eval",
     ("training",), r"djev|jevk5", "JevK5 night orchestrator"),
    ("harold", "Harold & X", r"harold|xbot|\bx-|-x-|twitter|xplainer|airesearch12|launch-sniper|twifork|owner-mention|tweet|follow",
     ("harold",), r"bh-xbot|harold|launch-sniper|xbot|jevbench-watch|codex-urgent|bh-bookmark",
     "Harold bot (@benchmarkheaven)"),
    ("site", "Benchmark Heaven site & SEO", r"\bbh-|benchmarkheaven|benchmark-heaven|site|seo|page|\bcr-?\d|iter\d|ux-|writer|hn-|search-console|numbers-audit",
     ("site",), r"bh-merge-queue|bh-thirdparty|benchmarkheaven|gated-run|ux-owner|bh-", "Benchmark Heaven merge queue"),
    ("jevbench", "JevBench releases & measurements", r"jevbench|jev-|werr|measure|release|round\d|scout|sealed|kushal|add-requests|paid-eval|autoloops|captain",
     ("releases", "measurements"), r"jevbench", "Release captain"),
    ("infra", "Agent infrastructure", r"agent|board|notify|quota|devin|codex|claude|limits|hermes|model-economy|telegram|rules|prompt|parallelism|union-alpha|opencode|workspace|florian-requests|status-overview|org|timeline|llm-health|engine",
     (), r"agent-board|agent-timeline|devin-usage|llm-health|telegram-reply|hermes|codex-auth|rc-watch|claude-rc|codex-rc|sent-watchdog|llm-router",
     "Hermes + Claude Code"),
    ("ops", "Sandy ops · disk, GPU, tunnel", r"disk|gpu|pod|tunnel|proxy|home-ip|egress|watchdog|backup|storage|cleanup|sandy|reaper|runpod|lium|infra|fritz|wireguard",
     ("infra",), r"gpu-|runpod-reaper|homeproxy|harold-homeproxy|backup|purge|offsite|diskwatch|sandy-|retention|bonsai-pod|restore-test|resource",
     "Sandy watchdog"),
    ("products", "Products & portfolio", r"pdfmint|docmint|mailmint|bookhost|postial|sandbox|stripe|fastlane|bonsai|who-is-right|whichmodel|router|toast|hautarzt|portfolio|venture|slop|finanz|h3|n8n|make|zapier|outreach",
     (), r"portfolio|fastlane|jev-router|bookstack|stripe|n8n|make-review|quota-warning|listing|goal-|socialmint|gauntlet",
     "Portfolio controller"),
    ("other", "Other", r"$^", (), r"$^", ""),
]
TOPIC_KEYS = [t[0] for t in TOPICS]
SLUG_TOPIC = {}
for _k, _t, _p, _slugs, _s, _o in TOPICS:
    for _s2 in _slugs:
        SLUG_TOPIC.setdefault(_s2, _k)

def topic_of(name, title="", board_slug=None):
    hay = f"{name} {title}".lower()
    # the job name is the strongest signal, the title only a tie-breaker
    for key, _t, pat, *_ in TOPICS:
        if re.search(pat, name.lower()):
            return key
    if board_slug and board_slug in SLUG_TOPIC:
        return SLUG_TOPIC[board_slug]
    for key, _t, pat, *_ in TOPICS:
        if re.search(pat, hay):
            return key
    return "other"

# ───────────────────────────── processes ─────────────────────────────
AGENT_RE = re.compile(r"^(?:\S*/)?(claude|codex|devin|opencode)(?:\s|$)|^node \S*/codex(?:\s|$)|^timeout .*?/devin\s")
NOISE_RE = re.compile(r"^(tee|timeout|sleep|cat|sudo -n -u sandy-mcp)\b|codex-code-mode-host|codex-desktop-mcp|"
                      r"mcp-server|sandy-deploy-mcp|/bin/bash -lc|^bash -lc|^/bin/bash /home/flori/bin/run-|"
                      r"^bash /home/flori/bin/run-|devin acp$|devin/cli/_versions/.*/bin/devin acp|^\(sd-pam\)|"
                      r"npm exec|mcp-remote|playwright-mcp|browsermcp|postgres-mcp|^sh -c ['\"]?mcp|/mcp\b|_mcp\b")
SHARED_UNITS = re.compile(r"^(codex-rc|claude-rc|hermes-[a-z-]+|user@\d+|init)\.(service|scope)$|^session-\d+\.scope$")

def exe_tokens(args):
    """The program actually running: the binary, or the script under node/python/bash."""
    t = args.split()
    if not t:
        return ""
    exe = t[0].rsplit("/", 1)[-1]
    if exe in ("node", "python3", "python", "bash", "sh", "timeout", "env") and len(t) > 1:
        rest = [x for x in t[1:4] if not x.startswith("-") and not re.fullmatch(r"\d+", x)]
        return exe + " " + " ".join(r.rsplit("/", 1)[-1] for r in rest[:1])
    return exe

def engine_of(args):
    e = exe_tokens(args).lower()
    for name in ("devin", "opencode", "codex", "claude", "hermes"):
        if re.search(rf"(?:^|\s){name}(?:\s|$)", e):
            return name
    return None

def model_of(args):
    m = re.search(r"(?:\s-m|--model)[ =]([A-Za-z0-9._:/-]+)", args)
    return m.group(1) if m else None

def label_of(args):
    a = args.strip()
    if "tg_watch" in a:
        return "waiting for Florian's Telegram reply"
    eng = engine_of(a) if AGENT_RE.search(a) else None
    if eng:
        mode = ("exec" if " exec" in a else "-p" if re.search(r"\s(-p|--print)\b", a) else
                "remote-control" if "remote-control" in a else "acp" if " acp" in a else
                "run" if " run " in a else "interactive")
        mdl = model_of(a)
        return f"{eng} {mode}" + (f" · {mdl}" if mdl else "")
    m = re.search(r"([A-Za-z0-9_.-]+\.(?:py|sh|mjs|js|ts))\b", a)
    if m:
        prog = a.split()[0].rsplit("/", 1)[-1]
        return f"{prog} {m.group(1)}"
    return a.split()[0].rsplit("/", 1)[-1][:40]

def short_cmd(a):
    """Command line without the prompt text agents receive as their last argument."""
    a = SENSITIVE.sub("[redacted]", a)
    if AGENT_RE.search(a):
        keep = []
        for tok in a.split():
            if len(" ".join(keep)) > 150 or (keep and not tok.startswith("-") and not keep[-1].startswith("-")
                                              and len(keep) > 2):
                break
            keep.append(tok)
        return " ".join(keep)[:160] + " …"
    return a[:160]

def read_procs():
    raw = sh(["ps", "-u", str(UID), "-o", "pid=,ppid=,etimes=,rss=,args="])
    procs = {}
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        pid = int(m[1])
        args = m[5]
        if args.startswith("ps -u"):
            continue
        try:
            cgpath = Path(f"/proc/{pid}/cgroup").read_text().strip()
            cg = cgpath.rsplit("/", 1)[-1]
            if "/user@" not in cgpath:
                cg = "system:" + cg           # cron.service, sshd etc.: shared by unrelated work
        except Exception:
            cg = ""
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except Exception:
            cwd = ""
        procs[pid] = {"pid": pid, "ppid": int(m[2]), "runtime_s": int(m[3]), "rss_mb": int(m[4]) // 1024,
                      "args": args, "unit": cg, "cwd": cwd}
    return procs

def jobdir_of_cwd(cwd):
    for base in (str(JOBS) + "/", str(HOME / "wt") + "/"):
        if cwd.startswith(base):
            name = cwd[len(base):].split("/", 1)[0]
            if base.endswith("/wt/"):
                # worktrees are named after the job; map back to its folder if it exists
                cands = sorted(JOBS.glob(name + "*"))
                return cands[0].name if cands else None
            return name
    return None

def assign_procs(procs, job_names):
    """job folder -> list of pids. Dedicated units by majority cwd, shared units per process."""
    by_unit = {}
    for p in procs.values():
        by_unit.setdefault(p["unit"], []).append(p)
    out, unit_of_job = {}, {}
    for unit, L in by_unit.items():
        if not unit or unit.startswith("system:") or SHARED_UNITS.match(unit):
            for p in L:
                jd = jobdir_of_cwd(p["cwd"])
                if jd and (AGENT_RE.search(p["args"]) or "tg_watch" in p["args"] or
                           any(AGENT_RE.search(procs.get(a, {}).get("args", "")) for a in ancestors(procs, p["pid"], 6))):
                    out.setdefault(jd, []).append(p["pid"])
                    unit_of_job.setdefault(jd, unit)
            continue
        votes = {}
        for p in L:
            jd = jobdir_of_cwd(p["cwd"])
            if jd:
                votes[jd] = votes.get(jd, 0) + 1
        base = unit.rsplit(".", 1)[0]
        jd = max(votes, key=votes.get) if votes else None
        if not jd:
            jd = next((j for j in job_names if j == base or re.sub(r"-\d{8}$", "", j) == base), None)
        if jd and (votes or any(AGENT_RE.search(p["args"]) for p in L)):
            out.setdefault(jd, []).extend(p["pid"] for p in L)
            unit_of_job[jd] = unit
    return out, unit_of_job

def ancestors(procs, pid, depth):
    out, cur = [], procs.get(pid, {}).get("ppid")
    while cur and depth > 0:
        out.append(cur)
        cur = procs.get(cur, {}).get("ppid")
        depth -= 1
    return out

def proc_tree(procs, pids):
    """Visible tree for one job: noise hidden, wrappers collapsed, subagents marked."""
    S = set(pids)
    kids = {}
    for pid in pids:
        kids.setdefault(procs[pid]["ppid"], []).append(pid)
    roots = [p for p in pids if procs[p]["ppid"] not in S]
    rows, main_engine, main_model = [], None, None
    subagents = 0
    waiting = False

    def visit(pid, depth, agent_depth):
        nonlocal main_engine, main_model, subagents, waiting
        p = procs[pid]
        a = p["args"]
        is_agent = bool(AGENT_RE.search(a)) and not NOISE_RE.search(a)
        hidden = bool(NOISE_RE.search(a)) or (is_agent and agent_depth > 0 and
                                              engine_of(a) == engine_of(procs[p["ppid"]]["args"]) and
                                              p["ppid"] in S and AGENT_RE.search(procs[p["ppid"]]["args"]))
        if "codex-linux-x64/vendor" in a or "/bin/codex-" in a and "code-mode" not in a:
            hidden = True                    # the native codex binary under its node wrapper
        if "tg_watch" in a:
            waiting = True
            hidden = False
        nd, nad = depth, agent_depth
        if not hidden:
            kind = "agent" if is_agent else ("wait" if "tg_watch" in a else "tool")
            if is_agent:
                if main_engine is None:
                    main_engine, main_model = engine_of(a), model_of(a)
                elif agent_depth > 0:
                    kind = "subagent"
                    subagents += 1
                nad = agent_depth + 1
            if not (kind == "wait" and any(r.get("kind") == "wait" and r["label"] == label_of(a) for r in rows)):
                rows.append({"pid": pid, "depth": depth, "kind": kind, "label": label_of(a),
                             "engine": engine_of(a) if is_agent else None,
                             "runtime_s": p["runtime_s"], "rss_mb": p["rss_mb"],
                             "cmd": short_cmd(a)})
            nd = depth + 1
        for c in sorted(kids.get(pid, []), key=lambda x: procs[x]["runtime_s"], reverse=True):
            visit(c, nd, nad)

    for r in sorted(roots, key=lambda x: procs[x]["runtime_s"], reverse=True):
        visit(r, 0, 0)
    merged, first = [], {}
    for r in rows:
        k = (r["kind"], r["label"])
        if r["kind"] == "tool" and k in first:
            f = first[k]
            f["count"] = f.get("count", 1) + 1
            f["rss_mb"] += r["rss_mb"]
            continue
        first[k] = r
        merged.append(r)
    rows = merged
    # keep it readable: agents + waits always, at most 8 tool rows
    tools = [r for r in rows if r["kind"] == "tool"]
    if len(tools) > 8:
        drop = {id(r) for r in sorted(tools, key=lambda r: r["rss_mb"])[: len(tools) - 8]}
        rows = [r for r in rows if id(r) not in drop]
    started = max((procs[p]["runtime_s"] for p in pids), default=0)
    return {"rows": rows, "engine": main_engine, "model": main_model, "subagents": subagents,
            "waiting": waiting, "runtime_s": started,
            "rss_mb": sum(procs[p]["rss_mb"] for p in pids), "proc_count": len(pids)}

# ───────────────────────────── systemd ─────────────────────────────
def user_units():
    try:
        L = json.loads(sh(["systemctl", "--user", "list-units", "--type=service,timer", "--all",
                           "--no-pager", "-o", "json"]) or "[]")
    except Exception:
        L = []
    return {u["unit"]: {"active": u.get("active"), "sub": u.get("sub"), "desc": u.get("description", "")} for u in L}

def unit_props(units):
    if not units:
        return {}
    raw = sh(["systemctl", "--user", "show", *units, "-p",
              "Id,RuntimeMaxUSec,ActiveEnterTimestamp,InactiveEnterTimestamp,Result,Environment", "--no-pager"])
    out, cur = {}, {}
    for line in raw.splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                out[cur["Id"]] = cur
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    return out

def ts_of(systemd_ts):
    if not systemd_ts or systemd_ts in ("n/a", "0"):
        return None
    try:
        return int(time.mktime(time.strptime(systemd_ts.rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S"))) - time.timezone \
            if systemd_ts.endswith("UTC") else int(time.mktime(time.strptime(systemd_ts.rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S")))
    except Exception:
        return None

# ───────────────────────────── job folders ─────────────────────────────
def first_heading(path):
    try:
        with open(path, errors="replace") as fh:
            for i, line in enumerate(fh):
                s = line.strip()
                if s.startswith("#"):
                    t = s.lstrip("# ").strip()
                    return re.sub(r"\s*\((\d{1,2} \w+ )?\d{4}[^)]*\)\s*$", "", t)[:140]
                if s and i > 3 and not s.startswith("#"):
                    return s[:140]
                if i > 12:
                    break
    except Exception:
        pass
    return ""

def scan_jobs(running, extra=()):
    now = time.time()
    out = {}
    try:
        entries = list(os.scandir(JOBS))
    except Exception:
        return out
    for d in entries:
        if not d.is_dir():
            continue
        name = d.name
        try:
            dm = d.stat().st_mtime
        except OSError:
            continue
        m = re.search(r"(\d{8})(?:T\d{4}Z)?$", name)
        dated = 0
        if m:
            try:
                dated = time.mktime(time.strptime(m.group(1), "%Y%m%d"))
            except Exception:
                pass
        if name not in running and name not in extra and dm < now - RECENT_S and dated < now - RECENT_S:
            continue
        files, last = {}, 0
        try:
            for f in os.scandir(d.path):
                if f.is_file():
                    st = f.stat()
                    if f.name != "BOARD-INBOX.md":      # the board appends here even after a job ended
                        last = max(last, st.st_mtime)
                    if f.name in ("PROMPT.md", "OUTPUT.md", "RESULT.md", "session.log", ".codex-attempt.log",
                                  ".devin-attempt.log", "BOARD-INBOX.md", "LAUNCHED-BY", "STATUS.md"):
                        files[f.name] = st.st_size
        except OSError:
            pass
        last = last or dm
        if name not in running and last < now - RECENT_S:
            continue
        if "PROMPT.md" not in files and name not in running and name not in extra:
            continue
        out[name] = {"dir": d.path, "files": files, "last_activity": int(last)}
    return out

def engine_from_files(name, files):
    if ".devin-attempt.log" in files:
        return "devin"
    if ".codex-attempt.log" in files:
        return "codex"
    if (HOME / ".claude/projects" / ("-home-flori-jobs-" + name)).exists():
        return "claude"
    head = ""
    try:
        with open(JOBS / name / "session.log", errors="replace") as fh:
            head = fh.read(400)
    except Exception:
        pass
    for e in ("devin", "codex", "opencode", "claude"):
        if f"run-{e}" in head or head.lower().startswith(e):
            return e
    return "claude" if "OUTPUT.md" in files else None

def tail_text(path, n=6000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > n:
                fh.seek(size - n)
            return fh.read().decode("utf-8", "replace")
    except Exception:
        return ""

BLOCKED_RE = re.compile(r"^\s*(?:\*\*)?(?:status|result|state)(?:\*\*)?\s*[:=]\s*(?:\*\*)?(blocked|waiting)", re.I | re.M)
ETA_RE = re.compile(r"\bETA\b[:\s~]*([^\n.;|]{3,40})")

# ───────────────────────────── parents ─────────────────────────────
PARENT_CACHE = {}
PARENT_LOCK = threading.Lock()
PARENT_CACHE_FILE = HOME / ".cache/agent-org-parents.json"
try:
    PARENT_CACHE.update(json.loads(PARENT_CACHE_FILE.read_text()))
except Exception:
    pass

LAPTOP = {"kind": "laptop", "name": "Claude Code (laptop)",
          "evidence": "no launch command found on Sandy; the laptop session starts jobs over SSH, which leaves no log here"}

def find_parent(name, unit, props, started_at, job_names):
    # 1) explicit marker
    marker = JOBS / name / "LAUNCHED-BY"
    if marker.exists():
        txt = marker.read_text(errors="replace").strip().splitlines()[0][:80] if marker.stat().st_size else ""
        if txt:
            return {"kind": classify_parent(txt), "name": txt, "evidence": "LAUNCHED-BY file", "job": txt if txt in job_names else None}
    env = (props or {}).get("Environment", "")
    m = re.search(r"AGENT_PARENT=(\S+)", env)
    if m:
        return {"kind": classify_parent(m.group(1)), "name": m.group(1), "evidence": "AGENT_PARENT in the unit"}
    # 2) process ancestry for shared units
    if unit:
        unit = unit.split(":", 1)[-1] if re.match(r"system:(codex-rc|claude-rc|session-)", unit) else unit
        if unit.startswith("codex-rc"):
            return {"kind": "remote", "name": "Codex remote control", "evidence": "runs inside codex-rc.service (Florian's phone/browser)"}
        if unit.startswith("claude-rc"):
            return {"kind": "remote", "name": "Claude remote control", "evidence": "runs inside claude-rc.service (Florian's phone/browser)"}
        if unit.startswith("system:"):
            return {"kind": "timer", "name": "cron", "evidence": f"runs under {unit[7:]}"}
        if unit.startswith("session-"):
            return {"kind": "laptop", "name": "SSH session (laptop)", "evidence": f"runs in login {unit}"}
        base = unit.rsplit(".", 1)[0]
        if base.startswith("portfolio-review-") and base != "portfolio-review-controller":
            return {"kind": "timer", "name": "portfolio-review-controller", "evidence": "portfolio rotation unit"}
    # 3) cached search result
    with PARENT_LOCK:
        if name in PARENT_CACHE:
            return PARENT_CACHE[name]
    return None

def classify_parent(txt):
    t = txt.lower()
    if "hermes" in t:
        return "hermes"
    if "laptop" in t:
        return "laptop"
    if "timer" in t or "cron" in t:
        return "timer"
    return "job"

def birth_times(dirs):
    out = {}
    for line in sh(["stat", "-c", "%W %Y %n", *dirs], timeout=20).splitlines():
        born, mod, path = line.split(" ", 2)
        out[Path(path).name] = int(born) if born not in ("0", "-") else int(mod)
    return out

def search_parents(jobs):
    """Background: find the systemd-run line that launched each job in another job's logs or Hermes' db.

    Only a real launch line counts (systemd-run ... --unit <unit> / ... jobs/<name>), and only in a job
    that already existed and was still active when the child started — a log that merely lists another
    job's command line (ps output, audits) is not a parent.
    """
    todo = [(n, j) for n, j in jobs.items() if n not in PARENT_CACHE]
    if not todo:
        return
    now = time.time()
    dirs = [d for d in JOBS.iterdir() if d.is_dir()]
    born = birth_times([str(d) for d in dirs])
    logs = {}
    for d in dirs:
        try:
            if d.stat().st_mtime < now - RECENT_S - 2 * 86400:
                continue
        except OSError:
            continue
        for f in ("session.log", ".codex-attempt.log", ".devin-attempt.log", "OUTPUT.md", "session-codex.log", "session2.log"):
            pth = d / f
            if pth.exists():
                logs.setdefault(d.name, []).append((str(pth), pth.stat().st_mtime))
    for name, j in todo[:60]:
        start = born.get(name) or j.get("started_at") or j.get("last_activity") or now
        unit = (j.get("unit") or re.sub(r"-\d{8}(T\d{4}Z)?$", "", name)).split(":")[-1].rsplit(".service", 1)[0]
        cands = [pth for jn, L in logs.items() if jn != name and born.get(jn, now) <= start + 60
                 for pth, mt in L if mt >= start - 60]
        found = None
        if cands:
            rx = (r"systemd-run[^\n]{0,600}?(--unit[ =]" + re.escape(unit) + r"(\.service)?[\s\"']|--unit[ =]"
                  + re.escape(name) + r"[\s\"']|jobs/" + re.escape(name) + r"[\s/\"'>])")
            hits = [h for h in sh(["rg", "-l", "--max-filesize", "80M", "-e", rx, *cands], timeout=30).split() if h]
            if hits:
                jn = Path(hits[0]).parent.name
                found = {"kind": "job", "name": jn, "job": jn, "evidence": f"systemd-run launch line in {jn}/{Path(hits[0]).name}"}
        if not found:
            try:
                con = sqlite3.connect(f"file:{HOME}/.hermes/state.db?mode=ro", uri=True, timeout=3)
                row = con.execute("select id from messages where timestamp between ? and ? and role='assistant' "
                                  "and tool_calls like '%systemd-run%' and tool_calls like ? limit 1",
                                  (start - 1800, start + 600, f"%{name}%")).fetchone()
                con.close()
                if row:
                    found = {"kind": "hermes", "name": "Hermes", "evidence": f"systemd-run in Hermes' session (message {row[0]})"}
            except Exception:
                pass
        with PARENT_LOCK:
            PARENT_CACHE[name] = found or dict(LAPTOP)
    try:
        PARENT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with PARENT_LOCK:
            PARENT_CACHE_FILE.write_text(json.dumps(PARENT_CACHE))
    except Exception:
        pass

# ───────────────────────────── board ─────────────────────────────
def board_info(job_names):
    """Latest entry per job author, thread titles, laptop sessions, questions to @florian."""
    res = {"by_job": {}, "threads": {}, "laptop": [], "questions": []}
    if not BOARD_DB.exists():
        return res
    try:
        con = sqlite3.connect(f"file:{BOARD_DB}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        for r in con.execute("select id,title,topic from threads"):
            res["threads"][r["id"]] = {"title": r["title"], "slug": r["topic"]}
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - RECENT_S - 86400))
        agent_job = {}
        for r in con.execute("select agent,jobdir from agents"):
            if r["jobdir"].startswith(str(JOBS) + "/"):
                agent_job[r["agent"]] = r["jobdir"][len(str(JOBS)) + 1:].split("/")[0]
        short = {re.sub(r"-\d{8}$", "", n): n for n in job_names}
        rows = con.execute("select id,thread_id,author,kind,body,created_at from entries where created_at>=? order by id",
                           (since,)).fetchall()
        counts = {}
        for r in rows:
            a = r["author"]
            ident = a.split(":", 1)[-1]
            jn = agent_job.get(a) or (ident if ident in job_names else short.get(ident))
            item = {"id": r["id"], "thread_id": r["thread_id"], "kind": r["kind"], "author": a,
                    "created_at": r["created_at"], "body": SENSITIVE.sub("[redacted]", r["body"])[:280],
                    "url": f"{BOARD_URL}/#/thread/{r['thread_id']}?e={r['id']}"}
            if jn:
                res["by_job"][jn] = item
                counts.setdefault(jn, {}).setdefault(r["thread_id"], 0)
                counts[jn][r["thread_id"]] += 1
            elif a.startswith("claude:laptop"):
                res.setdefault("_laptop", {})[a] = item
        for jn, c in counts.items():
            tid = max(c, key=c.get)
            res["by_job"][jn]["main_thread"] = tid
        for a, item in res.pop("_laptop", {}).items():
            res["laptop"].append({"name": a.split(":", 1)[1], "last": item})
        # questions to Florian that nobody has answered with a later entry in the same thread
        for r in con.execute("select e.id,e.thread_id,e.author,e.kind,e.body,e.created_at from entries e "
                             "join entry_addresses x on x.entry_id=e.id where x.address='florian' and e.kind='question' "
                             "and e.created_at>=? order by e.id desc limit 10",
                             (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400)),)):
            later = con.execute("select 1 from entries where thread_id=? and id>? and author like 'florian%' limit 1",
                                (r["thread_id"], r["id"])).fetchone()
            if not later:
                res["questions"].append({"text": r["body"][:300], "source": r["author"],
                                         "at": r["created_at"],
                                         "url": f"{BOARD_URL}/#/thread/{r['thread_id']}?e={r['id']}"})
        con.close()
    except Exception as e:
        res["error"] = str(e)[:200]
    return res

# ───────────────────────────── GPU cost ─────────────────────────────
def gpu_costs():
    out = {"by_job": {}, "today_total": None, "live": {}}
    try:
        st = json.loads((GPU / "monitor-state.json").read_text())
        out["by_job"] = st.get("spend_by_job_usd") or {}
        out["today_total"] = st.get("conservative_spend_usd")
        out["updated_at"] = st.get("updated_at")
        out["day"] = st.get("utc_day")
    except Exception:
        pass
    try:
        pods = {}
        for line in (GPU / "ledger.jsonl").read_text().splitlines():
            e = json.loads(line)
            key = e.get("pod_id") or e.get("reservation_id")
            pods.setdefault(key, {}).update(e)
        for p in pods.values():
            if p.get("closed") or p.get("event") in ("release", "released", "terminate", "terminated", "close"):
                continue
            j = p.get("job") or "unassigned"
            out["live"].setdefault(j, []).append({"provider": p.get("provider"), "name": p.get("pod_name"),
                                                  "hourly_usd": p.get("hourly_price_usd"),
                                                  "cap_usd": p.get("cost_cap_usd")})
    except Exception:
        pass
    return out

# ───────────────────────────── needs Florian ─────────────────────────────
STOP = set("the a an and or of to for in on at is are be with by from your you this that it not no nothing der die das und "
           "ist nicht für mit von zu im den dem ein eine".split())

COMMON = set("""jevbench benchmark heaven florian agent agents job jobs needs need please reply today check
bitte prüfen prüfe heute antwort antworte neue neuen new live site seite page review""".split())
GLOSSARY = {"schreibplatz": "writer slot", "schlüssel": "key", "erweiterung": "extension", "zugriff": "access",
            "freigeben": "approve approved", "freigabe": "approve approved", "bilder": "image", "bildmessung": "image measurement",
            "umwandlung": "conversion", "verlustfreie": "lossless", "konto": "account", "rechte": "rights",
            "bildrechte": "image rights", "rotieren": "rotate", "kündigen": "cancel", "bestätigen": "confirm"}
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}

def tokens(text):
    t = text.lower()
    for de, en in GLOSSARY.items():
        if de in t:
            t += " " + en
    return {w for w in re.findall(r"[a-zäöüß0-9]+", t) if len(w) >= 4 and w not in STOP and w not in COMMON}

def proper_tokens(text):
    """Names and nouns that identify a topic: capitalised words (not sentence starts) and glossary terms."""
    out = set()
    for m in re.finditer(r"(?<![.!?:]\s)(?<!^)\b([A-Z][A-Za-z0-9äöüÄÖÜ]+(?:-[A-Za-z0-9]+)*)", text):
        for w in re.split(r"-", m.group(1).lower()):
            if len(w) >= 4 and w not in COMMON and w not in STOP:
                out.add(w)
    low = text.lower()
    for de, en in GLOSSARY.items():
        if de in low:
            out |= set(en.split())
    return out

def resolved_entries():
    """(date, token set, explicit?) for each DECISIONS.md bullet plus explicit `resolved-keys:` phrases."""
    out = []
    try:
        txt = DECISIONS.read_text(errors="replace")
    except Exception:
        return out
    day = None
    for line in txt.splitlines():
        m = re.match(r"^##\s+(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", line)
        if m:
            try:
                day = time.mktime((int(m[3]), MONTHS[m[2].lower()], int(m[1]), 0, 0, 0, 0, 0, -1))
            except Exception:
                day = None
            continue
        k = re.match(r"^\s*[-*]?\s*resolved-keys\s*:\s*(.+)$", line, re.I)
        if k:
            for phrase in re.split(r"[,;]", k.group(1)):
                if len(phrase.strip()) > 3:
                    out.append((0, phrase.strip().lower(), True))
            continue
        if line.startswith("- ") and day:
            out.append((day, (tokens(line), proper_tokens(line)), False))
    return out

def is_resolved(text, at, entries):
    low = text.lower()
    tk, pk = tokens(text), proper_tokens(text)
    for day, val, explicit in entries:
        if explicit:
            if val in low:
                return True
            continue
        words, names = val
        shared = tk & words
        if at and day >= time.mktime(time.localtime(at)[:3] + (0, 0, 0, 0, 0, -1)) - 1 \
                and len(shared) >= 2 and shared & (pk | names):
            return True
    return False

def split_todos(text):
    """Pull the individual human todos out of one notify message (old and new format)."""
    t = text.strip()
    if re.match(r"^needs you:\s*nothing", t, re.I) or re.search(r"(für dich|braucht dich)[^\n]*:\s*nichts", t, re.I):
        return []
    block = None
    m = re.search(r"(?:🧑\s*Für dich|Braucht dich)\s*:?\s*\n(.+?)(?:\n\s*\n(?=\S*\s*(?:🤖|✅|Stand:))|\n🤖|\Z)", t, re.S)
    if m:
        block = m.group(1)
    elif re.match(r"^(needs your decision|needs you|action needed)\b", t, re.I) or t.startswith("🧑"):
        block = re.sub(r"^(needs your decision|needs you|action needed|🧑 DU BIST DRAN)\s*[:.]?\s*", "", t, flags=re.I)
    if block is None:
        # a direct question to Florian is a todo even without the "Needs you" header
        q = re.search(r"([^.!?\n]*\b(?:do you want|should i|shall i|would you like|can you|could you|please (?:reply|approve|confirm|decide)|"
                      r"möchtest du|soll ich|willst du|kannst du|bitte (?:antworte|bestätige|entscheide|gib))\b[^?\n]*\?)", t, re.I)
        if not q:
            return []
        return [SENSITIVE.sub("[redacted]", re.sub(r"^.*?:\s*", "", q.group(1).strip()) if len(q.group(1)) > 200 else q.group(1).strip())[:240]]
    items = [re.sub(r"^\s*(?:[•\-*]|\d+[.)])\s*", "", l).strip() for l in block.splitlines()
             if re.match(r"^\s*(?:[•\-*]|\d+[.)])\s+\S", l)]
    if not items:
        parts = [x.strip() for x in re.split(r"(?<=[.?!])\s+|\n", block.strip()) if x.strip()]
        first = " ".join(parts[:2]) if parts and len(parts[0]) < 30 else (parts[0] if parts else "")
        items = [first] if first else []
    return [SENSITIVE.sub("[redacted]", i)[:240] for i in items if len(i) > 8 and "↩" not in i and "👂" not in i][:6]

def needs_florian():
    now = time.time()
    out = {"items": [], "source": None, "checked_against": "DECISIONS.md"}
    ht = NOTIFY / "human-todo.json"
    if ht.exists():
        try:
            d = json.loads(ht.read_text())
            L = d.get("items", d) if isinstance(d, dict) else d
            for it in L:
                if it.get("resolved") or it.get("done"):
                    continue
                out["items"].append({"text": it.get("text") or it.get("title", ""), "why": it.get("why"),
                                     "steps": it.get("steps") or [], "minutes": it.get("minutes"),
                                     "source": it.get("source"), "at": it.get("created_at"), "kind": "todo"})
            out["source"] = str(ht)
        except Exception:
            pass
    if not out["source"]:
        out["source"] = "notify log (last 24 h, latest message per sender, unanswered)"
        latest = {}
        try:
            with open(NOTIFY / "log.jsonl", "rb") as fh:
                fh.seek(max(0, os.path.getsize(NOTIFY / "log.jsonl") - 400_000))
                lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
        except Exception:
            lines = []
        for line in lines:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("ts", 0) < now - 86400 or e.get("level") not in ("now", "urgent") or not e.get("sent", True):
                continue
            if e.get("source") == "notify-digest":
                continue
            src = e.get("source") or "?"
            items = split_todos(e.get("text", ""))
            # a newer message from the same sender replaces its earlier list, even if it asks for nothing
            if items or re.search(r"needs you|braucht dich|für dich|du bist dran", e.get("text", ""), re.I) or src in latest:
                latest[src] = {"items": items, "ts": e["ts"], "mid": e.get("message_id")}
        for src, v in latest.items():
            mid = v["mid"]
            if mid:
                rep = NOTIFY / "replies" / f"reply-{mid}.json"
                if rep.exists() and rep.stat().st_size > 2:
                    continue
            for it in v["items"]:
                out["items"].append({"text": it, "source": src, "at": int(v["ts"]), "kind": "notify",
                                     "message_id": mid})
    entries = resolved_entries()
    kept, dropped = [], 0
    for it in out["items"]:
        at = it.get("at")
        if isinstance(at, str):
            try:
                at = time.mktime(time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                at = now
        if is_resolved(it.get("text") or "", at or now, entries):
            dropped += 1
            continue
        kept.append(it)
    out["items"] = kept
    out["dropped_as_resolved"] = dropped
    out["checked_at"] = int(now)
    return out

# ───────────────────────────── standing automation ─────────────────────────────
def standing(units, crons, job_units=()):
    out = {k: [] for k in TOPIC_KEYS}
    seen = set()
    for name, u in units.items():
        base, typ = name.rsplit(".", 1)
        if typ == "service" and f"{base}.timer" in units:
            continue                              # shown via its timer
        if re.search(r"^(dbus|dconf|gvfs|pipewire|pulseaudio|plasma|xdg|kde|obex|gpg|dirmngr|ssh-agent|snapd|pk-|session-migration|xfce|docker)", base):
            continue
        if name in job_units or (re.search(r"-\d{8}(T\d{4}Z)?$|-c$|-devin\d*$|-0922$", base) and typ == "service"):
            continue                              # one-off jobs are listed as jobs
        for key, _t, _p, _s, spat, _o in TOPICS:
            if re.search(spat, base):
                if base in seen:
                    break
                seen.add(base)
                svc = units.get(f"{base}.service", u)
                state = ("failed" if svc.get("active") == "failed" else
                         "running" if svc.get("sub") == "running" else
                         "scheduled" if typ == "timer" and u.get("active") == "active" else svc.get("sub") or "?")
                out[key].append({"name": base, "type": typ, "state": state})
                break
    for c in crons:
        hay = f"{c.get('script', '')} {c.get('name', '')}"
        for key, _t, _p, _s, spat, _o in TOPICS:
            if key != "other" and re.search(spat, hay):
                out[key].append({"name": c.get("name"), "type": "cron", "state": c.get("human")})
                break
    return out

# ───────────────────────────── assemble ─────────────────────────────
_CACHE = {"t": 0, "data": None}
_LOCK = threading.Lock()
_SEARCHING = threading.Event()

def collect_topics(crons=None, max_age=10):
    with _LOCK:
        if _CACHE["data"] and time.time() - _CACHE["t"] < max_age:
            return _CACHE["data"]
        data = _collect(crons or [])
        _CACHE.update(t=time.time(), data=data)
        return data

def _collect(crons):
    now = time.time()
    procs = read_procs()
    all_names = {d.name for d in JOBS.iterdir() if d.is_dir()} if JOBS.exists() else set()
    assign, unit_of_job = assign_procs(procs, all_names)
    units = user_units()
    gpu = gpu_costs()
    jobs_fs = scan_jobs(set(assign), set(gpu["by_job"]) | set(gpu["live"]))
    names = set(jobs_fs) | set(assign)
    # units of recent jobs that are not running (to see failures)
    for n in names:
        if n not in unit_of_job:
            short = re.sub(r"-\d{8}(T\d{4}Z)?$", "", n)
            for cand in (f"{n}.service", f"{short}.service"):
                if cand in units:
                    unit_of_job[n] = cand
                    break
    props = unit_props(sorted({u for u in unit_of_job.values() if not SHARED_UNITS.match(u) and not u.startswith("system:")}))
    board = board_info(names)
    jobs = {}
    for n in names:
        fs = jobs_fs.get(n) or {"dir": str(JOBS / n), "files": {}, "last_activity": int(now)}
        files = fs["files"]
        tree = proc_tree(procs, assign[n]) if n in assign else None
        unit = unit_of_job.get(n)
        u = units.get(unit or "", {})
        pr = props.get(unit or "", {})
        title = first_heading(JOBS / n / "PROMPT.md") if "PROMPT.md" in files else ""
        b = board.get("by_job", {}).get(n)
        slug = board["threads"].get(b.get("main_thread"), {}).get("slug") if b else None
        running = bool(tree and tree["rows"])
        engine = (tree or {}).get("engine") or engine_from_files(n, files) or ("script" if running else None)
        status, reason = "done", ""
        result_tail = tail_text(JOBS / n / "RESULT.md", 3000) if "RESULT.md" in files else ""
        if running:
            status = "running"
            if tree["waiting"]:
                status, reason = "waiting", "waiting for Florian's Telegram reply"
        elif u.get("active") == "failed" or pr.get("Result") not in (None, "", "success"):
            status, reason = "failed", f"unit {unit} ended: {pr.get('Result') or u.get('sub')}"
        elif BLOCKED_RE.search(result_tail or tail_text(JOBS / n / "OUTPUT.md", 3000)):
            status, reason = "blocked", "its result says blocked"
        elif not result_tail and files.get("OUTPUT.md", 0) < 200:
            status, reason = "stopped", "ended without a result file"
        started = int(now - tree["runtime_s"]) if tree else (ts_of(pr.get("ActiveEnterTimestamp")) or None)
        eta = None
        rmax = pr.get("RuntimeMaxUSec", "infinity")
        if running and started and rmax not in ("", "infinity"):
            try:
                eta = {"at": started + int(rmax) // 1_000_000, "kind": "hard stop (unit limit)"}
            except ValueError:
                pass
        if running and not eta and engine == "devin" and started:
            eta = {"at": started + 7200, "kind": "hard stop (Devin 2 h timeout)"}
        if b and running:
            m = ETA_RE.search(b["body"])
            if m:
                eta = {"text": m.group(1).strip(), "kind": "from its last board entry"}
        cost = None
        if n in gpu["by_job"] or n in gpu["live"]:
            cost = {"today_usd": gpu["by_job"].get(n), "live_pods": gpu["live"].get(n, [])}
        j = {"id": n, "name": re.sub(r"-\d{8}(T\d{4}Z)?$", "", n), "date": (re.search(r"(\d{8})", n) or [None, None])[1],
             "title": title, "engine": engine, "model": (tree or {}).get("model"),
             "status": status, "reason": reason, "running": running, "unit": unit,
             "started_at": started, "last_activity": fs["last_activity"], "eta": eta,
             "procs": (tree or {}).get("rows", []), "subagents": (tree or {}).get("subagents", 0),
             "rss_mb": (tree or {}).get("rss_mb"), "proc_count": (tree or {}).get("proc_count", 0),
             "board": b, "board_slug": slug, "cost": cost,
             "files": {k: v for k, v in files.items() if k in ("OUTPUT.md", "RESULT.md", "PROMPT.md", "STATUS.md", "BOARD-INBOX.md")},
             "topic": topic_of(n, title, slug)}
        j["parent"] = find_parent(n, unit, pr, started, names)
        jobs[n] = j
    # parents found in other jobs' logs arrive asynchronously; never block a request on rg
    missing = {n: j for n, j in jobs.items() if not j["parent"]}
    if missing and not _SEARCHING.is_set():
        _SEARCHING.set()
        def run():
            try:
                search_parents(missing)
            finally:
                _SEARCHING.clear()
        threading.Thread(target=run, daemon=True).start()
    for j in jobs.values():
        if not j["parent"]:
            j["parent"] = {"kind": "pending", "name": "looking up…", "evidence": "search in progress"}
        p = j["parent"]
        if p.get("kind") == "job" and p.get("job") in jobs:
            jobs[p["job"]].setdefault("children", []).append(j["id"])
    std = standing(units, crons, set(unit_of_job.values()))
    topics = []
    for key, title, _p, slugs, _s, owner in TOPICS:
        L = [j for j in jobs.values() if j["topic"] == key]
        tids = [tid for tid, t in board["threads"].items() if t.get("slug") in slugs]
        laptop = [s for s in board["laptop"] if SLUG_TOPIC.get(board["threads"].get(s["last"]["thread_id"], {}).get("slug")) == key]
        run = [j for j in L if j["running"]]
        lead = next((j for j in sorted(run, key=lambda j: -(j["started_at"] or 0)) if re.search(r"captain|orchestrator|controller|lead", j["id"])), None)
        last_entry = max((j["board"] for j in L if j.get("board")), key=lambda e: e["id"], default=None)
        cost = sum((j["cost"] or {}).get("today_usd") or 0 for j in L)
        if not L and not std.get(key) and not laptop:
            continue
        topics.append({"key": key, "title": title, "owner": (lead["name"] + " (" + (lead["engine"] or "?") + ")") if lead else owner,
                       "owner_job": lead["id"] if lead else None,
                       "laptop_sessions": laptop, "threads": [{"id": t, "title": board["threads"][t]["title"],
                                                               "url": f"{BOARD_URL}/#/thread/{t}"} for t in tids],
                       "jobs": sorted((j["id"] for j in L), key=lambda n: (not jobs[n]["running"], -jobs[n]["last_activity"])),
                       "counts": {s: sum(1 for j in L if j["status"] == s) for s in ("running", "waiting", "blocked", "failed", "done", "stopped")},
                       "gpu_today_usd": round(cost, 2) if cost else None,
                       "last_entry": last_entry, "standing": std.get(key, [])})
    nf = needs_florian()
    nf["items"] += [{"text": q["text"], "source": q["source"], "at": q["at"], "url": q["url"], "kind": "board"}
                    for q in board.get("questions", [])]
    engines = {}
    for j in jobs.values():
        if j["running"]:
            engines[j["engine"] or "?"] = engines.get(j["engine"] or "?", 0) + 1
    return {"generated_at": int(now), "topics": topics, "jobs": jobs, "needs_florian": nf,
            "gpu": {"today_usd": gpu.get("today_total"), "day": gpu.get("day"), "updated_at": gpu.get("updated_at")},
            "engines_running": engines, "board_url": BOARD_URL,
            "laptop_sessions": board.get("laptop", []), "board_error": board.get("error")}

if __name__ == "__main__":
    import sys
    d = collect_topics()
    if "--summary" in sys.argv:
        for t in d["topics"]:
            print(f"\n## {t['title']} — owner {t['owner']} — {t['counts']} gpu {t['gpu_today_usd']}")
            for n in t["jobs"]:
                j = d["jobs"][n]
                print(f"  [{j['status']:8}] {n:48} {j['engine'] or '?':8} parent={j['parent']['name']:28} subs={j['subagents']} procs={len(j['procs'])} board={'#'+str(j['board']['id']) if j['board'] else '-'}")
            print("  standing:", ", ".join(f"{s['name']}({s['state']})" for s in t["standing"])[:300])
        print("\nNEEDS FLORIAN:", json.dumps(d["needs_florian"], ensure_ascii=False, indent=1)[:3000])
    else:
        print(json.dumps(d, indent=1, ensure_ascii=False)[:20000])
