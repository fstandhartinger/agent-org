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
    (r"claude-rc",                  "Leitung",      "Fernsteuerungs-Zentrale",
     "Florians Kanal vom Handy oder Browser auf den Server. Infrastruktur, keine Aufgabe."),
    (r"codex-rc",                   "Leitung",      "Fernsteuerung codex",
     "Dasselbe fuer codex-Sitzungen."),
    (r"hermes",                     "Leitung",      "Stellv. Product Owner",
     "Beaufsichtigt die Coding-Sessions, prueft Gmail, n8n, Make und Zapier im Auftrag."),
    (r"taoRecovery|tao-strategy",   "Forschung",    "Quant-Research",
     "Sucht eine belastbare TAO-Tradingstrategie und laesst sie adversarial pruefen."),
    (r"run-growth",                 "Produktion",   "Produktzyklus",
     "Bringt die Produkte voran: Gesundheit, Postfach, Review-Pipelines, Sichtbarkeit."),
    (r"goal-daily|goal-check",      "Produktion",   "Zielabgleich",
     "Prueft GOAL.md gegen die Wirklichkeit."),
    (r"n8n-template-watch|n8n-review-watch|n8n-docmint|n8n-template-views",
                                    "Vertrieb",     "Marktplatz-Waechter",
     "Meldet Bewegung bei n8n-Einreichungen. Meldet nur Aenderungen, nie Standmeldungen."),
    (r"make-review-watch",          "Vertrieb",     "Marktplatz-Waechter",
     "Beobachtet die Make.com-App-Review."),
    (r"quota-warning",              "Vertrieb",     "Kundenkontingent",
     "Warnt, wenn ein zahlendes Konto 80 Prozent seines Kontingents erreicht."),
    (r"sent-watchdog",              "Sicherheit",   "Postausgangs-Kontrolle",
     "Schlaegt Alarm, wenn Mail an Fremde rausgeht. Reissleine aus dem Vorfall vom 28.08."),
    (r"session-reaper|stale-session|reap-orphans",
                                    "Betrieb",      "Sitzungshygiene",
     "Beendet abgelaufene und festgefahrene Sitzungen."),
    (r"session-restart",            "Betrieb",      "Wiederanlauf",
     "Startet Sitzungen neu, die am Limit gescheitert sind."),
    (r"gmail-inbox-watch",          "Vertrieb",     "Posteingang",
     "Prueft das Postfach auf Aufgaben und erledigt sie."),
    (r"ready-to-earn",              "Produktion",   "Verkaufsbereitschaft",
     "Prueft, ob die Produkte wirklich verkaufsfaehig sind."),
    (r"make-core-abo",              "Betrieb",      "Kostenkontrolle",
     "Prueft, wann das Make-Abo kuendbar ist."),
    (r"[Ss]ecurity",                "Sicherheit",   "Sicherheitsaudit",
     "Woechentliche Pruefung, nur lesend."),
]

def classify(name, cmd=""):
    hay = f"{name} {cmd}"
    for pat, dept, role, desc in ROLES:
        if re.search(pat, hay):
            return dept, role, desc
    return "Sonstiges", "unklassifiziert", ""

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
                        "trigger": "dauerhaft", "can_kill": True, "can_prompt": True})
    return out

def agent_processes():
    raw = sh("ps -eo pid,ppid,etimes,rss,args --no-headers 2>/dev/null")
    procs = []
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, ppid, et, rss, args = int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5]
        if not re.search(r"claude|codex|opencode|hermes", args):
            continue
        if re.search(r"grep|--no-headers", args):
            continue
        tool = ("claude" if "claude" in args else
                "codex" if "codex" in args else
                "opencode" if "opencode" in args else "hermes")
        procs.append({"kind": "process", "id": f"pid:{pid}", "pid": pid, "ppid": ppid,
                      "tool": tool, "runtime_s": et, "rss_mb": rss // 1024,
                      "cmd": args[:150], "can_kill": True, "can_prompt": False})
    return procs

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

def collect():
    sessions = tmux_sessions()
    procs = agent_processes()
    # Prozesse den Sitzungen zuordnen: ueber die tmux-Serverprozesse
    return {"generated_at": int(time.time()),
            "sessions": sessions, "processes": procs,
            "cron": cron_jobs(), "hermes": hermes_jobs(),
            "tokens": token_usage(),
            "host": os.uname().nodename}

if __name__ == "__main__":
    print(json.dumps(collect(), indent=2, ensure_ascii=False))
