# Agent Org

A live org chart of everything running as an agent on the Sandy server: tmux
sessions across all sockets, cron jobs, Hermes' own jobs, and the running
processes of Claude, codex, opencode and Hermes.

**The chart is measured, not maintained.** Department and role come from rules in
`lib/collect.py`; everything else is read live from `tmux`, `ps`, the crontab and
`~/.hermes/cron/jobs.json`. It therefore cannot go stale — a hand-drawn diagram
would have been wrong within a day.

## Views
- **List** — grouped by department, subagents indented under their parent.
- **Org chart** — the classic boxes-and-lines layout, Florian at the top,
  departments below, units below those. Horizontally scrollable on a phone.
- **Show subagents** — every agent process that is actually running, with its
  parent, runtime, memory and token usage. A subagent here is simply an agent
  process whose parent is itself an agent process; that is more truthful than any
  maintained list, because it shows what was actually spawned.
- **Live** — a Server-Sent-Events stream pushes a freshly measured state every few
  seconds. Toggle it off to save the connection.

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
