#!/usr/bin/env python3
"""Erhebt den Live-Zustand aller Agenten auf Sandy und baut daraus einen Baum.

Grundgedanke: Das Organigramm wird NICHT gepflegt, sondern gemessen. Jede
Abteilung und jede Rolle ist eine Zuordnung ueber Regeln (ROLES weiter unten);
was tatsaechlich laeuft, kommt aus tmux, ps, crontab und Hermes' Jobdatei.
So kann das Bild nicht veralten.

Vorsicht bei den Transkripten: das sind teils hunderte MB grosse .jsonl mit
sehr langen Zeilen. Sie werden ausschliesslich vom ENDE her gelesen (tail_bytes),
nie ganz geladen — ein voller Scan hat auf dieser Maschine schon einmal die
WSL-Instanz an den Rand des Absturzes gebracht.
"""
import json, os, re, subprocess, time
from pathlib import Path

HOME = Path("/home/flori")
UID = os.getuid()

def sh(cmd, timeout=25):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""

# ─────────────────────────── Rollenmodell ───────────────────────────
# (Muster, Abteilung, Rolle, Beschreibung). Erster Treffer gewinnt.
ROLES = [
    (r"claude-rc",                  "Leadership",      "Remote-control hub",
     "Florian's channel from phone or browser to the server. Infrastructure, not a task."),
    (r"codex-rc",                   "Leadership",      "Remote-control (codex)",
     "The same, for codex sessions."),
    (r"hermes",                     "Leadership",      "Deputy product owner",
     "Supervises the coding sessions; checks Gmail, n8n, Make and Zapier on Florian's behalf."),
    (r"taoRecovery|tao-strategy",   "Research",    "Quant research",
     "Searches for a defensible TAO trading strategy and has it adversarially reviewed."),
    (r"run-growth",                 "Production",   "Product cycle",
     "Moves the products forward: health, inbox, review pipelines, discoverability."),
    (r"goal-daily|goal-check",      "Production",   "Goal audit",
     "Checks GOAL.md against reality."),
    (r"n8n-template-watch|n8n-review-watch|n8n-docmint|n8n-template-views",
                                    "Distribution",     "Marketplace watcher",
     "Reports movement on n8n submissions. Only changes, never status pings."),
    (r"make-review-watch",          "Distribution",     "Marketplace watcher",
     "Watches the Make.com app review."),
    (r"quota-warning",              "Distribution",     "Customer quota",
     "Warns when a paying account reaches 80% of its quota."),
    (r"sent-watchdog",              "Security",   "Outbound mail guard",
     "Raises the alarm when mail goes to outsiders. The tripwire from the 28 Aug incident."),
    (r"session-reaper|stale-session|reap-orphans",
                                    "Operations",      "Session hygiene",
     "Ends expired and stuck sessions."),
    (r"session-restart",            "Operations",      "Restart handler",
     "Restarts sessions that failed on the rate limit."),
    (r"gmail-inbox-watch",          "Distribution",     "Inbox watcher",
     "Checks the inbox for tasks and handles them."),
    (r"ready-to-earn",              "Production",   "Sales readiness",
     "Checks whether the products are genuinely ready to sell."),
    (r"make-core-abo",              "Operations",      "Cost control",
     "Checks when the Make subscription can be cancelled."),
    (r"task-discover|discover",     "Distribution", "Discoverability",
     "SEO and marketplace listings so the products can be found."),
    (r"task-migrate|migrate",       "Operations",   "Migration",
     "Moves services between hosts and repoints their configuration."),
    (r"mailmint",                   "Distribution", "MailMint push",
     "Drives MailMint towards its first paying outside customer."),
    (r"audit-goal",                 "Production",   "Claim audit",
     "Checks the claims in GOAL.md against measurable reality."),
    (r"make-review",                "Distribution", "Make submission",
     "Carries the Make.com app through its review."),
    (r"n8n-template",               "Distribution", "n8n templates",
     "Prepares and submits n8n workflow templates."),
    (r"[Ss]ecurity",                "Security",   "Security audit",
     "Weekly review, read-only."),
]

def classify(name, cmd=""):
    hay = f"{name} {cmd}"
    for pat, dept, role, desc in ROLES:
        if re.search(pat, hay):
            return dept, role, desc
    return "Unassigned", "unclassified", ""

def head_comment(path):
    """Erste erklaerende Kommentarzeile eines Skripts."""
    try:
        for line in Path(path).read_text(errors="replace").splitlines()[:8]:
            s = line.strip()
            if s.startswith("#") and not s.startswith("#!") and len(s) > 4:
                return s.lstrip("# ").strip()[:160]
    except Exception:
        pass
    return ""

# ─────────────────────────── Erhebung ───────────────────────────
def tmux_sessions():
    out = []
    sock_dir = Path(f"/tmp/tmux-{UID}")
    if not sock_dir.exists():
        return out
    for sock in sock_dir.iterdir():
        raw = sh(f"tmux -S {sock} list-sessions "
                 f"-F '#{{session_name}}|#{{session_created}}|#{{session_windows}}' 2>/dev/null")
        for line in raw.strip().splitlines():
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name, created, wins = parts[0], int(parts[1]), parts[2]
            dept, role, desc = classify(name)
            out.append({"kind": "session", "id": f"tmux:{sock.name}:{name}",
                        "name": name, "socket": sock.name,
                        "started": created, "runtime_s": int(time.time()) - created,
                        "windows": wins, "dept": dept, "role": role, "desc": desc,
                        "trigger": "persistent", "can_kill": True, "can_prompt": True})
    return out

def agent_processes():
    """Alle Agentenprozesse mit Eltern-Kind-Beziehung.

    Ein Subagent ist hier schlicht ein Agentenprozess, dessen Elternteil selbst
    ein Agentenprozess ist. Das ist praeziser als jede gepflegte Liste: es zeigt,
    was TATSAECHLICH gestartet wurde, nicht was jemand vorhatte.
    """
    raw = sh("ps -eo pid,ppid,etimes,rss,args --no-headers 2>/dev/null")
    procs = {}
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, ppid, et, rss, args = int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5]
        if re.search(r"grep|--no-headers|ps -eo", args):
            continue
        if not re.search(r"\bclaude\b|\bcodex\b|opencode|hermes", args):
            continue
        tool = ("hermes" if "hermes" in args else
                "codex" if "codex" in args else
                "opencode" if "opencode" in args else "claude")
        # Sitzungskennung, wo sie in der Kommandozeile steht (Claude SDK, codex resume)
        sid = None
        ms = re.search(r"(cse_[A-Za-z0-9]+)", args) or re.search(r"resume\s+([0-9a-f-]{8,})", args)
        if ms:
            sid = ms.group(1)
        procs[pid] = {"kind": "process", "id": f"pid:{pid}", "pid": pid, "ppid": ppid,
                      "tool": tool, "runtime_s": et, "rss_mb": rss // 1024,
                      "session_id": sid, "cmd": args[:170],
                      "label": short_label(args, tool),
                      "can_kill": True, "can_prompt": False, "children": []}
    # Doppelte Eintraege desselben Dienstes zusammenfassen: der Fernsteuerungs-Hub
    # erscheint als tmux-Wrapper UND als eigentlicher Prozess. Nur den echten behalten.
    seen = {}
    for pid, p in list(procs.items()):
        if p["cmd"].startswith("/usr/bin/tmux ") or " tmux -L " in p["cmd"]:
            del procs[pid]; continue
        key = (p["label"], p["tool"])
        if key in seen and "app-server" not in p["cmd"]:
            # gleicher Dienst, mehrere Prozesse: der aelteste ist der Traeger
            keep = seen[key]
            if procs[keep]["runtime_s"] >= p["runtime_s"]:
                procs[keep]["instances"] = procs[keep].get("instances", 1) + 1
                del procs[pid]; continue
            p["instances"] = procs[keep].get("instances", 1) + 1
            del procs[keep]
        seen[key] = pid

    # Kinder anhaengen; Wurzeln sind die, deren Elternteil kein Agent ist
    roots = []
    for pid, p in procs.items():
        parent = procs.get(p["ppid"])
        if parent:
            parent["children"].append(pid)
            p["is_subagent"] = True
        else:
            p["is_subagent"] = False
            roots.append(pid)
    for pid, p in procs.items():
        p["child_count"] = len(p["children"])
    return {"by_pid": procs, "roots": roots}

def short_label(args, tool):
    """Sprechender Kurzname statt der vollen Kommandozeile."""
    if "remote-control" in args:
        return f"{tool} remote-control"
    if "app-server" in args:
        return f"{tool} app-server"
    if "--print" in args or "-p " in args:
        return f"{tool} headless task"
    if "exec" in args and tool == "codex":
        return "codex exec"
    if "gateway" in args:
        return "hermes gateway"
    m = re.search(r"scripts/([a-z0-9_.-]+)\.py", args)
    if m:
        return m.group(1)
    return f"{tool} process"

def cron_jobs():
    out = []
    raw = sh("crontab -l 2>/dev/null")
    if not raw.strip():
        # Im Container gibt es kein crontab-Binary; die Spool-Datei tut es auch.
        for cand in ("/var/spool/cron/crontabs/flori", "/host/crontab"):
            try:
                raw = Path(cand).read_text(); break
            except Exception:
                pass
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        sched, cmd = " ".join(parts[:5]), parts[5]
        m = re.search(r"(/home/flori/[^\s>]+\.(?:sh|py))", cmd)
        script = m.group(1) if m else cmd[:60]
        out.append({"kind": "cron", "id": f"cron:{os.path.basename(script)}",
                    "name": os.path.basename(script), "schedule": sched,
                    "human": human_schedule(sched), "script": script,
                    "desc": head_comment(script) if m else "",
                    "trigger": "cron", "can_kill": False, "can_prompt": False,
                    **dict(zip(("dept", "role", "_d"), classify(os.path.basename(script), cmd)))})
    for j in out:
        j["desc"] = j["desc"] or j.pop("_d", "")
        j.pop("_d", None)
    return out

def human_schedule(sched):
    f = sched.split()
    if len(f) < 5:
        return sched
    minute, hour = f[0], f[1]
    if hour.startswith("*/"):
        return f"alle {hour[2:]} h"
    if minute.startswith("*/"):
        return f"alle {minute[2:]} min"
    if hour == "*":
        return "stuendlich"
    if f[4] != "*":
        return "woechentlich"
    return f"taeglich {hour.zfill(2)}:{minute.zfill(2)}"

def hermes_jobs():
    p = HOME / ".hermes/cron/jobs.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    out = []
    for j in d.get("jobs", []):
        s = j.get("schedule") or {}
        expr = s.get("expr") or s.get("display") or "?"
        name = j.get("name", "?")
        dept, role, desc = classify(name, j.get("prompt", "")[:400])
        out.append({"kind": "hermes_job", "id": f"hermes:{j.get('id', name)}",
                    "name": name, "schedule": expr, "human": human_schedule(expr),
                    "desc": desc or (j.get("prompt", "")[:150]),
                    "dept": dept, "role": role, "trigger": "cron (Hermes)",
                    "can_kill": False, "can_prompt": False})
    return out

def tail_bytes(path, n=60000):
    """Nur das Ende einer Datei lesen — Transkripte sind zu gross zum Laden."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > n:
                fh.seek(size - n)
                fh.readline()          # angebrochene Zeile verwerfen
            return fh.read().decode("utf-8", "replace")
    except Exception:
        return ""

def token_usage():
    """Kumulierte Token je Claude-Projekt, aus dem Ende der juengsten Transkripte."""
    root = HOME / ".claude/projects"
    out = {}
    if not root.exists():
        return out
    files = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            try:
                files.append((f.stat().st_mtime, f))
            except OSError:
                pass
    files.sort(reverse=True)
    for mtime, f in files[:12]:
        tot_in = tot_out = 0
        for line in tail_bytes(f).splitlines():
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            u = (d.get("message") or {}).get("usage") or d.get("usage") or {}
            tot_in = max(tot_in, u.get("input_tokens", 0) or 0)
            tot_out += u.get("output_tokens", 0) or 0
        if tot_in or tot_out:
            out[f.stem[:8]] = {"input": tot_in, "output": tot_out,
                               "project": f.parent.name, "mtime": int(mtime)}
    return out


def hermes_node():
    """Hermes als eigener Ast, nicht nur als Sammlung von Jobs.

    Erkennung ueber den Prozess, NICHT ueber `systemctl --user`: dieser Dienst
    laeuft als System-Unit und hat keinen Zugriff auf die Nutzer-Sitzung des
    Nutzers flori. Am 31.08. blieb Hermes deshalb unsichtbar, obwohl er lief.
    """
    procs = []
    for line in sh("ps -eo pid,etimes,rss,args --no-headers 2>/dev/null").splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)", line)
        if m and "hermes" in m[4] and "grep" not in m[4]:
            procs.append((int(m[1]), int(m[2]), int(m[3]) // 1024, m[4]))
    if not procs:
        return None
    gateway = next((p for p in procs if "gateway" in p[3]), procs[0])
    parts = []
    for key, label in (("gateway", "gateway"), ("dashboard", "dashboard"), ("browser", "browser")):
        if any(key in p[3] for p in procs):
            parts.append(label)
    return {"kind": "agent", "id": "hermes:gateway",
            "name": "Hermes", "dept": "Leadership", "role": "Deputy product owner",
            "desc": "Supervises the coding sessions; checks Gmail, n8n, Make and Zapier "
                    "on Florian's behalf. Owns the jobs listed under him.",
            "trigger": "persistent", "host": "sandy",
            "services": {k: "running" for k in parts} or {"gateway": "running"},
            "pid": gateway[0], "runtime_s": gateway[1], "rss_mb": gateway[2],
            "proc_count": len(procs),
            "can_kill": False, "can_prompt": True, "prompt_via": "hermes",
            "spawns": "few · own cron jobs"}

# Der Laptop laesst sich von hier aus nicht messen — dieser Dienst laeuft auf
# Sandy. Er wird deshalb als bekannter Knoten deklariert und ausdruecklich als
# solcher markiert, damit niemand ihn fuer eine Messung haelt.
LAPTOP_NODES = [
    {"kind": "declared", "id": "laptop:claude-code", "name": "Claude Code",
     "dept": "Leadership", "role": "Cross-cutting assistant",
     "desc": "Florian's assistant on his laptop. Drives the sessions on Sandy over SSH, "
             "coordinates with Hermes, and handles the work that needs judgement. "
             "Not measurable from here - declared, not observed.",
     "host": "laptop", "trigger": "interactive", "spawns": "few - Task/opencode/codex",
     "can_kill": False, "can_prompt": False},
    {"kind": "declared", "id": "laptop:browser", "name": "agent-browser",
     "dept": "Operations", "role": "Logged-in browser",
     "desc": "Drives Florian's signed-in sessions (X, Zendesk, Make) from the laptop.",
     "host": "laptop", "trigger": "on demand",
     "can_kill": False, "can_prompt": False},
]


def tag_host(items, host="sandy"):
    for i in items:
        i.setdefault("host", host)
    return items


# ── Kanaele ────────────────────────────────────────────────────────────────
# Florians Bild, und es ist das richtige: es gibt genau ZWEI Wege, ueber die er
# Auftraege in das Projekt gibt — diesen Claude-Code-Chat auf seinem Laptop und
# den Hermes-Agenten auf Sandy. Alles andere haengt an einem der beiden oder
# laeuft autonom weiter, nachdem es einmal von dort angestossen wurde.
#
# Bis 31.08. stand Hermes als eine Einheit unter fuenf in "Leadership". Das war
# sachlich vertretbar und trotzdem irrefuehrend: es verbarg, dass er kein
# Untergebener ist, sondern ein gleichrangiger Eingang.
CHANNELS = {
    "claude": {"id": "channel:claude", "name": "Claude Code",
               "role": "Prompt channel · Florian's laptop",
               "desc": "Florian talks to it directly. Drives the sessions on Sandy over "
                       "SSH, sets up and maintains the cron jobs, coordinates with Hermes.",
               "host": "laptop"},
    "hermes": {"id": "channel:hermes", "name": "Hermes",
               "role": "Prompt channel · deputy product owner",
               "desc": "Florian messages it from his phone. Supervises the coding sessions "
                       "and checks Gmail, n8n, Make and Zapier on his behalf. Owns its own jobs.",
               "host": "sandy"},
    "autonomous": {"id": "channel:autonomous", "name": "Running autonomously",
               "role": "Started once, still going",
               "desc": "Work that no longer needs a channel: it was kicked off from one of "
                       "the two and now runs on its own until its end date.",
               "host": "sandy"},
}

def channel_of(item):
    """Welchem Kanal gehoert ein Eintrag? Ehrlich zugeordnet, nicht geraten."""
    name = (item.get("name") or "") + " " + (item.get("role") or "")
    if item.get("kind") == "hermes_job" or "Hermes" in name:
        return "hermes"
    if item.get("host") == "laptop":
        return "claude"
    if re.search(r"tao|Quant research", name, re.I):
        return "autonomous"
    # Die Fernsteuerungs-Naben und alle cron-Jobs wurden ueber den Claude-Kanal
    # eingerichtet und werden von dort gepflegt.
    return "claude"

def collect():
    tree = agent_processes()
    procs = tree["by_pid"]
    toks = token_usage()
    # Token den Prozessen zuordnen, wo die Sitzungskennung passt
    for p in procs.values():
        sid = p.get("session_id")
        if sid:
            for key, t in toks.items():
                if sid.startswith(key) or key.startswith(sid[:8]):
                    p["tokens"] = t
                    break
    hn = hermes_node()
    sess, crons, hjobs = tag_host(tmux_sessions()), tag_host(cron_jobs()), tag_host(hermes_jobs())
    for it in sess + crons + hjobs + LAPTOP_NODES + ([hn] if hn else []):
        it["channel"] = channel_of(it)
    if hn:
        hn["channel"] = "hermes"          # Hermes IST der Kanal, nicht sein Insasse
    return {"generated_at": int(time.time()),
            "channels": CHANNELS,
            "hosts": {"sandy": "Sandy · Hetzner server", "laptop": "Florian's laptop"},
            "laptop": LAPTOP_NODES,
            "sessions": sess,
            "processes": list(procs.values()),
            "process_roots": tree["roots"],
            "cron": crons, "hermes": hjobs,
            "hermes_node": hn,
            "tokens": toks,
            "host": os.uname().nodename}


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2, ensure_ascii=False))
