"""Org map: the organisation behind everything that runs on Sandy, LeChonk and sandy-prod.

Florian at the top, the chief of staff below him, then the teams (each with its lead jobs, their
workers, standing automation and live production apps), and a separate band of cross-cutting
functions (security guards, watchdogs, messaging, coordination, backups) that serve every team.

Like the rest of the app it is measured, not maintained: jobs come from topics.collect_topics,
workers from <job>/workers/<slice>/, standing automation from systemd on Sandy and LeChonk, live
apps from the Coolify labels on sandy-prod. Only the team and guard catalogues below are rules.
Names, short job titles and states only — no prompt bodies, board messages, environment or secrets.
"""
import json, os, re, shutil, subprocess, threading, time
from pathlib import Path

import topics as T

HOME = Path("/home/flori")
JOBS = HOME / "jobs"
UID = os.getuid()
ENV = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{UID}",
           DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{UID}/bus")
DAY = 86400
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,150}")

# --- catalogues ---------------------------------------------------------------------------------
# Order matters: the first team whose pattern matches a job, unit or app name owns it.
TEAMS = [
    ("cos", "Chief of staff office", "🧭", "Florian's deputies: asks, follow-ups, night watch, digests",
     r"^cos-|chief|night-watch|night-deputy|morning-briefing|^florian-|^ask-|^asks-|deputy"),
    ("wallet", "Wallet & Finance", "💶", "Wallets, TAO trading, invoices, revenue and tax",
     r"wallet|financ|^tao-|tao-trader|tao-dashboard|finanz|stripe|invoice|^tax|-tax-|revenue|reaxsign|payment|refund|treasury"),
    ("sn92", "SN92 / REAX", "🏁", "Subnet 92 competitions, house models and releases",
     r"sn92|reax|rx92|rp2p|rxnode|generalist|training-push|house-model|netuid|bonsai|subnet"),
    ("dm", "Decision Models & router", "🧠", "Decision Models product, System1 API, LLM router",
     r"s1m|system1|decisionmodel|decision-model|decisions-bench|^dmh|^dm-|llm-router|auto-router|mine-eu|antseed|"
     r"jev-router|whichmodel|msft-decision|^decision"),
    ("mkt", "Marketing, Ads & X", "📣", "X accounts (Harold, bots), ads, SEO, launches, outreach",
     r"xbot|harold|^x-|-x-|(^|-)ads(-|$)|launch|seo|marketing|outreach|interview|bookmark|explainer|newsletter|"
     r"social|leadgen|directory|followups|discord|community"),
    ("bench", "JevBench & Benchmark Heaven", "📊", "Benchmarks, measurements, releases, the BH site",
     r"jev|bench|^bh-|-bh-|benchmark|fastlane|uc11|imagejev|model-market|priority|leaderboard|(^|-)eval"),
    ("products", "Products & portfolio", "🧩", "Mint apps, BookHost, Postial and smaller sites",
     r"mint|bookhost|postial|portfolio|(^|-)sale(-|$)|h3-studio|hautarzt|philosophen|prompt-theater|ai-slop|who-is-right|"
     r"live-fact|agent-as-a-service|mailposture|gpuserverhub|tag-janitor|domain"),
    ("infra", "Infra & servers", "🛠", "Sandy, LeChonk, sandy-prod, GPUs, disks, agent tooling",
     r"urgent-autofix|umami|lechonk|sandy|disk|gpu|migration|backup|fleet|home-?browser|proxy|(^|-)prod(-|$|\d|uction)|agent|hermes|codex|claude|devin|"
     r"opencode|job|llm|mail|cadvisor|coolify|storagebox|session|continuity|tailscale|postgres|pgbouncer|quota|"
     r"router|worker|lane|cloud|pod|vllm|model|server|infra"),
]
TEAM_RE = [(k, re.compile(p, re.I)) for k, _, _, _, p in TEAMS]

# Cross-cutting functions: (group key, title, icon, explanation, [(item name, unit regex, what it does)])
GUARDS = [
    ("security", "Security guards", "🛡", "Stop damage before it happens", [
        ("Egress guard", r"^egress-guard\.service$", "blocks scanning and unexpected outbound traffic"),
        ("Lane tripwire", r"^agentlane-tripwire\.timer$", "alerts when an isolated agent lane leaves its sandbox"),
        ("Lane gateway", r"^agentlane-(lanegw|proxy|nft)\.service$", "isolated network lanes for untrusted reads"),
        ("Signer tripwire", r"^reaxsign-tripwire\.timer$", "watches the SN92 award signer"),
        ("Wallet tripwire", r"^reaxwallet-tripwire\.timer$", "watches the wallet service"),
        ("Security watch", r"^sec-watch\.timer$", "daily security checks"),
        ("Audit log", r"^auditd\.service$", "kernel audit of secret files"),
        ("fail2ban", r"^fail2ban\.service$", "bans brute-force logins"),
        ("PaaS firewall", r"^sandy-paas-(firewall|container-policy)\.service$", "firewall and container policy for hosted apps"),
        ("X network guard", r"^xbot-net-guard\.timer$", "keeps the X bots on their own network path"),
        ("Build guard", r"^sandy-build-guard\.timer$", "stops runaway builds"),
    ]),
    ("watch", "Watchdogs", "👁", "Notice when something stalls or breaks", [
        ("Night watch", r"^night-watch-tick\.timer$", "checks every track overnight"),
        ("Ask watchdog", r"^ask-watchdog\.timer$", "sends an investigator when one of Florian's asks stalls"),
        ("Urgent autofix", r"^urgent-autofix\.timer$", "picks up urgent breakages automatically"),
        ("Job wake / resume", r"^(job-wake|claude-job-resume)\.timer$", "restarts parked jobs that got an answer"),
        ("Disk guard", r"^(disk-watch|sandy-disk-80-guard|job-log-archive-guard)\.timer$", "keeps the disks below 80 %"),
        ("GPU reaper", r"^gpu-(reaper|cost-monitor)\.timer$", "stops idle GPU pods and tracks spend"),
        ("LLM health", r"^llm-health(-union-alpha)?\.timer$", "probes the model endpoints"),
        ("Uptime", r"^(mint-uptime|bh-site-uptime|s1m-redirect-uptime)\.timer$", "probes the public sites"),
        ("LeChonk watch", r"^lechonk-(watch|pg-watch)\.timer$", "health of the workshop server"),
        ("Quota alerts", r"^(codex-left-alert|codex-resets-watch|codex-urgent|devin-usage-log)\.timer$", "engine limits"),
    ]),
    ("comms", "Messages & Telegram", "📨", "How agents reach Florian", [
        ("Telegram broker", r"^telegram-reply-broker\.service$", "routes Florian's Telegram replies"),
        ("Reply dispatcher", r"^notify-reply-dispatcher\.service$", "resumes the job that asked"),
        ("Hermes", r"^hermes-gateway\.service$", "Florian's phone channel"),
        ("Digests", r"^(florian-progress-digest|agent-board-digest|morning-briefing|florian-followups-digest)\.timer$",
         "08:00 / 13:00 / 19:00 summaries"),
    ]),
    ("coord", "Coordination", "🔗", "How agents work together", [
        ("Agent board", r"^agent-board-(view\.service|tick\.timer)$", "shared message board"),
        ("Timeline", r"^agent-timeline-(web\.service|collector\.timer)$", "what happened when"),
        ("Org chart", r"^agent-org\.service$", "this page"),
        ("LeChonk sync", r"^lechonk-(sync|jobs-retention)\.timer$", "syncs job folders between the hosts"),
        ("Decisions log", r"^decisions-snapshot\.timer$", "snapshots DECISIONS.md"),
        ("Keepalives", r"^keepalive@.+\.timer$", "restart long jobs until FINAL DONE"),
        ("Remote control", r"^(claude-rc|codex-rc)\.service$", "Florian's remote sessions"),
    ]),
    ("backup", "Backups", "💾", "Copies away from the servers", [
        ("PaaS backup", r"^sandy-paas-(backup|offsite-backup)\.timer$", "Coolify apps and databases"),
        ("Offsite R2", r"^fleet-offsite-r2-backup\.timer$", "offsite copy"),
        ("Control-plane standby", r"^controlplane-standby\.timer$", "hourly Sandy snapshot to LeChonk"),
        ("Prod backup pull", r"^sandy-prod-backup-pull\.timer$", "pulls sandy-prod backups"),
        ("DB backups", r"^s1m-prod-db-backup\.timer$", "production database dumps"),
        ("Storage Box", r"^storagebox-daily-claude(-retry)?\.timer$", "daily archive to the Storage Box"),
        ("Backup watch", r"^offsite-backup-watch\.timer$", "alerts when a backup is late"),
    ]),
]
GUARD_RES = [re.compile(p) for _, _, _, _, items in GUARDS for _, p, _ in items]

# Chief of staff: who stands between Florian and the teams.
COS_MEMBERS = [
    ("Chief of staff", "Claude Code on Florian's laptop", None, "laptop"),
    ("CoS deputy", "hourly deputy (Opus)", r"^cos-deputy-opus\.timer$", None),
    ("Reply priority", "answers Florian first", r"^cos-deputy-reply-priority\.timer$", None),
    ("Night deputy", "guards all tracks 22:30–07:30", r"^cos-night-deputy\.timer$", None),
    ("Hermes", "deputy product owner, phone channel", r"^hermes-gateway\.service$", None),
]

# Units that are plumbing of the OS or desktop, not agent work.
NOISE = re.compile(r"^(dbus|dirmngr|gpg|keyboxd|pk-|snapd|launchpadlib|plasma-|kde-|xfce|obex|xdg|pulse|pipewire|"
                   r"systemd|session-migration|docker|containerd|xvfb|sddm|haveged|lm-sensors|apparmor|ufw|"
                   r"tailscaled|certbot|wg-quick|postfix|sysstat|prometheus|mdmonitor|mdcheck|apt|man-db|motd|"
                   r"e2scrub|fstrim|logrotate|dpkg|update-notifier|ubuntu|ua-|getty|user@|cron|rsyslog|polkit)")

# --- small helpers ------------------------------------------------------------------------------
def sh(cmd, timeout=15, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env or ENV).stdout
    except Exception:
        return ""

def team_of(name):
    for k, rx in TEAM_RE:
        if rx.search(name):
            return k
    return None

def short(name):
    return re.sub(r"-\d{8}(T\d{4,6}Z?)?$", "", name)

def engine_badge(text, fallback=None):
    """Opus / Sonnet / Codex / Devin / ... from an .engine line, with a short detail."""
    t = (text or "").lower()
    detail = ""
    m = re.search(r"gpt-[\w.]+", t)
    if "devin" in t:
        k = "devin"
        detail = "Opus" if "opus" in t else "Sonnet" if "sonnet" in t else ""
    elif "fable" in t:
        k = "fable"
    elif "opus" in t:
        k = "opus"
    elif "sonnet" in t:
        k = "sonnet"
    elif "haiku" in t:
        k = "haiku"
    elif "codex" in t or m:
        k = "codex"
        detail = ("Sol" if re.search(r"\bsol\b|-sol\b", t) else "Luna" if "luna" in t else "Astra" if "astra" in t
                  else (m.group(0) if m else ""))
    elif re.search(r"\b(opencode|kimi|glm|deepseek|mimo|bunny)\b", t):
        k = "opencode"
    else:
        k = {"claude": "claude", "codex": "codex", "devin": "devin", "opencode": "opencode",
             "script": "script"}.get(fallback or "", None)
    acc = re.search(r"account ([abc])\b", t)
    return {"key": k, "detail": detail, "account": acc.group(1).upper() if acc else ""}

def read_small(p, n=400):
    try:
        with open(p, errors="replace") as fh:
            return fh.read(n).strip()
    except Exception:
        return ""

def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False

# --- systemd on both hosts ----------------------------------------------------------------------
LIST_UNITS = "systemctl {user} list-units --type=service,timer --all --no-pager -o json"

def parse_units(raw, host, scope):
    try:
        L = json.loads(raw or "[]")
    except Exception:
        return {}
    return {u["unit"]: {"unit": u["unit"], "active": u.get("active"), "sub": u.get("sub"),
                        "host": host, "scope": scope} for u in L if u.get("load") != "not-found"}

def sandy_units():
    out = parse_units(sh(LIST_UNITS.format(user="--user").split()), "sandy", "user")
    out.update({k: v for k, v in parse_units(sh(LIST_UNITS.format(user="").split()), "sandy", "system").items()
                if k not in out})
    return out

_REMOTE = {"t": 0, "data": None}
_REMOTE_LOCK = threading.Lock()
REMOTE_TTL = 60

def lechonk_state():
    """systemd units, running job units, load and disk on LeChonk (one ssh round trip, cached)."""
    with _REMOTE_LOCK:
        if _REMOTE["data"] is not None and time.time() - _REMOTE["t"] < REMOTE_TTL:
            return _REMOTE["data"]
    script = ("echo '#SYS'; systemctl list-units --type=service,timer --all --no-pager -o json; "
              "echo; echo '#USER'; systemctl --user list-units --type=service,timer --all --no-pager -o json; "
              "echo; echo '#LOAD'; cat /proc/loadavg; nproc; echo '#DISK'; df -P / | tail -1; "
              "echo '#MEM'; free -m | awk '/Mem:/{print $2, $7}'; "
              "echo '#JOBS'; systemctl list-units --state=active,activating --no-legend --plain 'job-*' | awk '{print $1}'")
    raw = sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "lechonk", script], timeout=12)
    data = {"reachable": bool(raw.strip()), "units": {}, "job_units": set(), "load": None, "cpus": None, "disk_pct": None, "mem": None}
    if raw.strip():
        parts = dict(re.findall(r"#(\w+)\n(.*?)(?=\n#\w+\n|\Z)", raw, re.S))
        data["units"] = parse_units(parts.get("SYS", "").strip(), "lechonk", "system")
        data["units"].update({k: v for k, v in parse_units(parts.get("USER", "").strip(), "lechonk", "user").items()
                              if k not in data["units"]})
        ld = parts.get("LOAD", "").split()
        if len(ld) >= 6:
            data["load"], data["cpus"] = float(ld[0]), int(ld[5])
        dk = parts.get("DISK", "").split()
        if len(dk) >= 5:
            data["disk_pct"] = int(dk[4].rstrip("%"))
        data["job_units"] = {re.sub(r"\.service$", "", x) for x in parts.get("JOBS", "").split()}
        mm = parts.get("MEM", "").split()
        if len(mm) == 2:
            data["mem"] = {"total_gb": round(int(mm[0]) / 1024), "free_gb": round(int(mm[1]) / 1024)}
    with _REMOTE_LOCK:
        _REMOTE.update(t=time.time(), data=data)
    return data

_PROD = {"t": 0, "data": None}
PROD_TTL = 300

def prod_apps():
    """Coolify app names on sandy-prod with their health (read-only docker ps, cached 5 min)."""
    if _PROD["data"] is not None and time.time() - _PROD["t"] < PROD_TTL:
        return _PROD["data"]
    try:
        r = subprocess.run([str(HOME / "bin/sandy-prod-docker"), "ps", "-a", "--format",
                            '{{.Label "coolify.resourceName"}}|{{.Names}}|{{.State}}|{{.Status}}'],
                           capture_output=True, text=True, timeout=20, env=ENV)
        raw, ok = r.stdout, r.returncode == 0
    except Exception:
        raw, ok = "", False
    apps = {}
    for line in raw.splitlines():
        name, cname, state, status = (line.split("|") + ["", "", "", ""])[:4]
        name = name or cname
        if not name or re.fullmatch(r"[a-z0-9]{24}(-\d+)?", name) or name in ("coolify-proxy",):
            continue
        st = ("failed" if state in ("dead", "restarting") or "unhealthy" in status
              or (state == "exited" and not status.startswith("Exited (0)")) else
              "running" if state == "running" else "stopped")
        prev = apps.get(name)
        if not prev or prev["state"] != "running":
            apps[name] = {"name": name, "state": st, "host": "prod"}
    data = {"reachable": ok, "apps": sorted(apps.values(), key=lambda a: a["name"])}
    _PROD.update(t=time.time() - (0 if ok else PROD_TTL - 30), data=data)
    return data

def unit_state(u, units):
    """running / ok / failed / stopped for a service or a timer (judged by its service's last run)."""
    if u["unit"].endswith(".timer"):
        svc = units.get(u["unit"][:-6] + ".service") or {}
        if svc.get("active") == "failed" or svc.get("sub") == "auto-restart":
            return "failed"
        if svc.get("active") in ("active", "activating"):
            return "running"
        return "ok" if u.get("active") == "active" else "stopped"
    if u.get("active") == "failed" or u.get("sub") == "auto-restart":
        return "failed"
    if u.get("active") in ("active", "activating", "reloading"):
        return "running"
    return "stopped"

# --- jobs, hosts and workers --------------------------------------------------------------------
def job_host(jobdir, lechonk_jobs=None):
    h = read_small(Path(jobdir) / ".host", 200)
    if h.startswith("lechonk"):
        m = re.search(r"\bunit (\S+)", h)
        if lechonk_jobs is not None and m:
            return "lechonk", re.sub(r"\.service$", "", m.group(1)) in lechonk_jobs
        return "lechonk", None           # LeChonk unreachable: don't guess
    if "was lechonk" in h:
        return "lechonk", False
    return "sandy", None

def workers_of(jobdir, lead_host, lead_running):
    wd = Path(jobdir) / "workers"
    out = []
    try:
        slices = sorted(p for p in wd.iterdir() if p.is_dir())
    except Exception:
        return out
    for w in slices:
        eng = read_small(w / ".engine", 200) or read_small(w / ".engine-requested", 200)
        exitc = read_small(w / ".exit", 20)
        pid = read_small(w / ".pid", 20)
        host = "lechonk" if ("on LeChonk" in eng or lead_host == "lechonk") else "sandy"
        if exitc != "":
            status = "done" if exitc.strip() == "0" else "failed"
        elif pid and host == "sandy" and pid_alive(pid):
            status = "running"
        elif host == "lechonk" and lead_running:
            status = "running"
        else:
            status = "stopped"
        try:
            last = int(max((f.stat().st_mtime for f in w.iterdir() if f.is_file()), default=w.stat().st_mtime))
        except Exception:
            last = None
        out.append({"slice": w.name, "status": status, "host": host, "engine": engine_badge(eng),
                    "last_activity": last})
    return out

def job_entry(j, now, lechonk_jobs=None):
    jd = JOBS / j["id"]
    host, live_remote = job_host(jd, lechonk_jobs)
    status = j.get("status") or "done"
    if live_remote and status not in ("running", "waiting", "blocked"):
        status = "running"               # lechonk-job keeps no agent process on Sandy
    eng = read_small(jd / ".engine", 200)
    w = workers_of(jd, host, status in ("running", "waiting"))
    live = [x for x in w if x["status"] in ("running", "failed")]
    fin = sorted((x for x in w if x not in live), key=lambda x: -(x["last_activity"] or 0))
    w = live + fin[:12]
    return {"id": j["id"], "name": short(j["id"]), "workers_finished": len(fin), "title": T.SENSITIVE.sub("[masked]", (j.get("title") or "")[:140]), "status": status,
            "reason": T.SENSITIVE.sub("[masked]", j.get("reason") or "")[:200], "host": host, "engine": engine_badge(eng or j.get("model"), j.get("engine")),
            "started_at": j.get("started_at"), "last_activity": j.get("last_activity"), "eta": j.get("eta"),
            "subagents": j.get("subagents") or 0, "files": sorted((j.get("files") or {}).keys()),
            "board": {"url": (j.get("board") or {}).get("url")} if (j.get("board") or {}).get("url") else None,
            "gpu_today_usd": (j.get("cost") or {}).get("today_usd"), "workers": w,
            "parent": ((j.get("parent") or {}).get("job") or (j.get("parent") or {}).get("name")) if (j.get("parent") or {}).get("kind") == "job" else None}

def visible(job, now):
    if job["status"] in ("running", "waiting", "blocked"):
        return True
    if job["status"] == "failed":
        return now - (job["last_activity"] or 0) < 2 * DAY
    return now - (job["last_activity"] or 0) < DAY

def needs_summary(nf):
    items = (nf or {}).get("items") or []
    return {"count": len(items), "first": T.SENSITIVE.sub("[masked]", str(items[0].get("text", ""))[:160]) if items else ""}

def safe(fn, fallback):
    try:
        return fn()
    except Exception:
        return fallback

# --- main ---------------------------------------------------------------------------------------
_CACHE = {"t": 0, "data": None}
_LOCK = threading.Lock()

_REFRESHING = threading.Event()

def _refresh(crons_fn):
    try:
        with _LOCK:
            data = _collect(crons_fn() if crons_fn else None)
            _CACHE.update(t=time.time(), data=data)
    finally:
        _REFRESHING.clear()

def collect_orgmap(crons_fn=None, max_age=20):
    """Serve the last measurement at once and refresh it in the background when it is older than max_age."""
    if _CACHE["data"] is None:
        _REFRESHING.set()
        _refresh(crons_fn)
    elif time.time() - _CACHE["t"] > max_age and not _REFRESHING.is_set():
        _REFRESHING.set()
        threading.Thread(target=_refresh, args=(crons_fn,), daemon=True).start()
    return _CACHE["data"]

def _collect(crons):
    now = time.time()
    tv = T.collect_topics(crons)
    lc = safe(lechonk_state, {"reachable": False, "units": {}, "job_units": set(), "load": None, "cpus": None, "disk_pct": None})
    prod = safe(prod_apps, {"reachable": False, "apps": []})
    units = safe(sandy_units, {})
    all_units = list(units.values()) + list(lc["units"].values())
    by_host = {"sandy": units, "lechonk": lc["units"]}

    # jobs -> teams (lead jobs; a job started by another job nests under it)
    lj = lc["job_units"] if lc["reachable"] else None
    jobs = {jid: job_entry(j, now, lj) for jid, j in tv["jobs"].items()}
    # ids end up inside onclick handlers in the page: keep only plain folder names
    shown = {jid: j for jid, j in jobs.items() if visible(j, now) and SAFE_ID.fullmatch(jid)}
    for j in shown.values():
        topic = tv["jobs"][j["id"]].get("topic")
        j["team"] = team_of(j["id"]) or {"jevbench": "bench", "imagejevbench": "bench", "site": "bench",
                                         "harold": "mkt", "products": "products", "infra": "infra",
                                         "ops": "infra"}.get(topic, "other")
    children = {}
    for j in shown.values():
        if j["parent"] and j["parent"] in shown:
            children.setdefault(j["parent"], []).append(j["id"])
            j["child"] = True
    for pid, kids in children.items():
        shown[pid]["subjobs"] = kids

    # standing automation per team, cross-cutting guards separately
    job_units = {f"{jid}.service" for jid in tv["jobs"]} | {f"{short(jid)}.service" for jid in tv["jobs"]}
    standing = {}
    seen = set()
    for u in all_units:
        n = u["unit"]
        base = re.sub(r"\.(service|timer)$", "", n)
        if NOISE.match(n) or n in job_units or any(rx.search(n) for rx in GUARD_RES):
            continue
        # a timer stands for its service; a service only on its own when nothing schedules it
        if n.endswith(".service") and (base + ".timer") in by_host[u["host"]]:
            continue
        if n.endswith(".service") and u.get("active") not in ("active", "activating", "failed"):
            continue          # one-off units that ran and ended
        if n.endswith(".timer") and u.get("active") != "active" and u.get("sub") != "running":
            continue          # elapsed one-shot timers
        tk = team_of(base)
        if not tk:
            continue
        key = (u["host"], base)
        if key in seen:
            continue
        seen.add(key)
        disp = re.sub(r"@([0-9a-f]{8})[0-9a-f-]{20,}$", r"@\1…", base)
        standing.setdefault(tk, []).append({"name": disp, "kind": n.rsplit(".", 1)[1], "host": u["host"],
                                            "state": unit_state(u, by_host[u["host"]])})
    for c in crons or []:
        tk = team_of(c.get("name", ""))
        if tk:
            standing.setdefault(tk, []).append({"name": c.get("name"), "kind": "cron", "host": "sandy", "state": "ok"})

    apps = {}
    for a in prod.get("apps", []):
        apps.setdefault(team_of(a["name"]) or "products", []).append(a)

    guards = []
    for gk, title, icon, desc, items in GUARDS:
        gi = []
        for name, pat, what in items:
            rx = re.compile(pat)
            hit = [u for u in all_units if rx.search(u["unit"]) and not (u["unit"].endswith(".service") and
                   re.sub(r"\.service$", ".timer", u["unit"]) in by_host[u["host"]])]
            if not hit:
                continue
            states = [unit_state(u, by_host[u["host"]]) for u in hit]
            st = ("failed" if "failed" in states else "running" if "running" in states else
                  "ok" if "ok" in states else "stopped")
            failed_units = sorted({u["unit"] for u, s in zip(hit, states) if s == "failed"})
            gi.append({"name": name, "what": what, "state": st, "hosts": sorted({u["host"] for u in hit}),
                       "units": len(hit), "failed": failed_units[:5]})
        guards.append({"key": gk, "title": title, "icon": icon, "desc": desc, "items": gi})

    cos = []
    for name, role, pat, host in COS_MEMBERS:
        if not pat:
            cos.append({"name": name, "role": role, "state": "ok", "hosts": [host]})
            continue
        rx = re.compile(pat)
        hit = [u for u in all_units if rx.search(u["unit"])]
        if hit:
            st = unit_state(hit[0], by_host[hit[0]["host"]])
            cos.append({"name": name, "role": role, "state": st, "hosts": sorted({u["host"] for u in hit})})

    teams = []
    for k, title, icon, desc, _ in TEAMS + [("other", "Unsorted", "🗂", "Jobs no team rule matched yet", "")]:
        tj = sorted((j for j in shown.values() if j.get("team") == k), key=lambda j: (
            {"running": 0, "waiting": 1, "blocked": 2, "failed": 3}.get(j["status"], 4), -(j["last_activity"] or 0)))
        counts = {s: sum(1 for j in tj if j["status"] == s) for s in ("running", "waiting", "blocked", "failed", "done", "stopped")}
        counts["workers_running"] = sum(1 for j in tj for w in j["workers"] if w["status"] == "running")
        st = sorted(standing.get(k, []), key=lambda s: ({"failed": 0, "running": 1}.get(s["state"], 2), s["name"]))
        ap = apps.get(k, [])
        if not (tj or st or ap):
            continue
        teams.append({"key": k, "title": title, "icon": icon, "desc": desc, "jobs": [j["id"] for j in tj],
                      "counts": counts, "standing": st, "apps": ap})

    la = os.getloadavg()
    du = shutil.disk_usage("/")
    hosts = {
        "sandy": {"label": "Sandy", "role": "control plane · browsers · timers · Docker",
                  "reachable": True, "load": round(la[0], 1), "cpus": os.cpu_count(),
                  "disk_pct": round(du.used * 100 / du.total),
                  "jobs_running": sum(1 for j in jobs.values() if j["status"] in ("running", "waiting") and j["host"] == "sandy"),
                  "units": sum(1 for u in units.values() if u.get("active") == "active" and not NOISE.match(u["unit"]))},
        "lechonk": {"label": "LeChonk", "role": "agent workshop · default for new jobs",
                    "reachable": lc["reachable"], "load": lc["load"], "cpus": lc["cpus"], "disk_pct": lc["disk_pct"],
                    "jobs_running": sum(1 for j in jobs.values() if j["status"] in ("running", "waiting") and j["host"] == "lechonk"),
                    "units": sum(1 for u in lc["units"].values() if u.get("active") == "active" and not NOISE.match(u["unit"]))},
        "prod": {"label": "sandy-prod", "role": "production only · customer apps",
                 "reachable": prod["reachable"], "apps": len(prod["apps"]),
                 "apps_failed": sum(1 for a in prod["apps"] if a["state"] == "failed")},
    }
    return {"generated_at": int(now), "hosts": hosts, "cos": cos, "guards": guards, "teams": teams,
            "jobs": shown, "needs_florian": needs_summary(tv.get("needs_florian")), "board_url": tv.get("board_url"),
            "engines_running": tv.get("engines_running")}
