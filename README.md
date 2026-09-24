# Agent Org

A live org chart of everything running as an agent on the Sandy server: tmux
sessions across all sockets, cron jobs, Hermes' own jobs, and the running
processes of Claude, codex, opencode and Hermes.

**The chart is measured, not maintained.** Department and role come from rules in
`lib/collect.py`; everything else is read live from `tmux`, `ps`, the crontab and
`~/.hermes/cron/jobs.json`. It therefore cannot go stale — a hand-drawn diagram
would have been wrong within a day.

## Views
- **Topics** (default, since 24 Sep 2026) — every running and recent job under
  `~/jobs`, grouped by topic (JevBench, ImageJevBench, Harold & X, Benchmark Heaven
  site, agent infrastructure, Sandy ops, training, products). Each topic shows its
  owner, running/waiting/blocked/failed/done counts, GPU spend, the last board entry
  and its standing automation (timers, services, cron). Each job shows engine
  (Codex/Claude/Devin/OpenCode/script), who started it, status, ETA, GPU cost, its
  process tree with subagents, links to OUTPUT.md/RESULT.md/PROMPT.md and its board
  thread. A "Needs you" strip at the top lists only Florian's open todos.
  Dark/light toggle; mobile layout. Built by `lib/topics.py`, served at `/api/topics`.
- **List** — grouped by department, subagents indented under their parent.
- **Org chart** — the classic boxes-and-lines layout, Florian at the top,
  departments below, units below those. Horizontally scrollable on a phone.
- **Show subagents** — every agent process that is actually running, with its
  parent, runtime, memory and token usage. A subagent here is simply an agent
  process whose parent is itself an agent process; that is more truthful than any
  maintained list, because it shows what was actually spawned.
- **Live** — a Server-Sent-Events stream pushes a freshly measured state every few
  seconds. Toggle it off to save the connection.

## Topic view: where each field comes from
- **Jobs**: `~/jobs/<name>-<date>/` active in the last 3 days, plus anything running.
- **Running / engine / subagents**: `ps` + `/proc/<pid>/cgroup` + cwd. A dedicated
  user unit (`systemd-run --user --unit <job>`) belongs to the job its processes work
  in; shared units (codex-rc, claude-rc, SSH sessions) are split per process by cwd.
  A subagent is an agent process below the job's main agent.
- **Status**: running; waiting (a Telegram reply watcher is alive); failed (unit
  failed); blocked (`Status: blocked` in RESULT/OUTPUT); done; stopped (no result).
- **Started by**: `LAUNCHED-BY` file in the job folder or `AGENT_PARENT=` in the unit
  environment if present; else process ancestry (codex-rc/claude-rc = remote control,
  session scope = SSH); else a `systemd-run` launch line in another job's logs or in
  Hermes' state.db (background search, cached in `~/.cache/agent-org-parents.json`);
  else "Claude Code (laptop)" — its SSH commands leave no log on Sandy. The evidence
  is shown next to every parent.
- **Board**: `~/.agent-board/board.db` read-only (last entry per job, topic threads,
  laptop sessions, unanswered questions to @florian).
- **GPU cost**: `~/.local/state/gpu-pods/monitor-state.json` (spend per job, UTC day)
  and `ledger.jsonl` (live pods).
- **Needs you**: `~/.notify/human-todo.json` when it exists (written by `notify`),
  otherwise the latest "Needs you"/"🧑 Für dich" list or direct question per sender in
  the notify log of the last 24 h, minus answered ones; every item is checked against
  `/home/flori/DECISIONS.md` (`resolved-keys:` plus bullets dated on/after the item).
- `/api/file?job=&name=` serves only OUTPUT.md, RESULT.md, PROMPT.md, STATUS.md and
  BOARD-INBOX.md of a job (tail 200 kB, secrets masked), token required.

## Deploying
Code changes go live with `sudo systemctl restart agent-org` (host service). The
Coolify app `agent-org` only rebuilds the nginx proxy in `proxy/`.

## Badges
`RUNNING` · `CRON + interval` · `SPAWNS` (few via opencode/codex, or many — the TAO
lab runs 6 in parallel) · `SUBAGENT` · `INFRASTRUCTURE` (must not be killed).

## Architecture

    Host (systemd: agent-org.service, port 8899)
      └── server.py + lib/collect.py     sees tmux, ps, crontab
    Container (Coolify, nginx)
      └── proxies to 172.30.1.1:8899, provides domain and TLS

The service deliberately does **not** run in the container: the host's tmux
sockets, process list and crontab are invisible from inside one. Note the gateway
address — the usual Docker bridge `172.17.0.1` does not exist on this machine;
`docker0` sits on `172.30.0.1` and the container hangs in the coolify network with
gateway `172.30.1.1`. Measured, not assumed. `/api/stream` is passed through with
buffering disabled, otherwise no event would ever arrive.

## Access
`ORG_TOKEN` in `/etc/agent-org.env` (mode 600). Without a token only `/healthz`
answers. The interface can kill sessions, so it must not sit open on the internet.

## Safety note
Claude transcripts are read from the **end only** (`tail_bytes`). A full scan of
those files once pushed this machine to the edge of an out-of-memory crash.
