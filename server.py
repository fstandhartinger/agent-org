#!/usr/bin/env python3
"""Agentenzentrale — zeigt und steuert alles, was auf Sandy als Agent laeuft.

Der Zustand wird bei jedem Aufruf frisch gemessen (siehe lib/collect.py), nicht
gepflegt. Aktionen (Sitzung beenden, Prompt schicken) laufen ueber tmux, weil
das derselbe Weg ist, den ein Mensch am Terminal nehmen wuerde.

Zugang ueber ORG_TOKEN. Ohne Token nur Lesen von /healthz — die Oberflaeche kann
Sitzungen beenden, das darf nicht offen im Netz stehen.
"""
import hmac, json, os, re, subprocess, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent / "lib"))
import collect as C

PORT = int(os.environ.get("PORT", "3000"))
TOKEN = os.environ.get("ORG_TOKEN", "")
STATIC = Path(__file__).parent / "static"

def authorised(handler):
    if not TOKEN:
        return True                     # kein Token gesetzt => offen (nur lokal sinnvoll)
    got = handler.headers.get("X-Org-Token", "")
    if not got:
        got = parse_qs(urlparse(handler.path).query).get("t", [""])[0]
    return hmac.compare_digest(got, TOKEN)

def tmux(sock, *args, timeout=20):
    cmd = ["tmux", "-S", f"/tmp/tmux-{os.getuid()}/{sock}", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            return self._send(200, json.dumps({"ok": True, "ts": int(time.time())}))
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/org":
            if not authorised(self):
                return self._send(401, json.dumps({"error": "token required"}))
            try:
                return self._send(200, json.dumps(C.collect(), ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)[:300]}))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not authorised(self):
            return self._send(401, json.dumps({"error": "token required"}))
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad json"}))

        if path == "/api/kill":
            sid = str(body.get("id", ""))
            m = re.fullmatch(r"tmux:([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)", sid)
            if m:
                sock, name = m.groups()
                # Verlauf sichern, bevor etwas verschwindet
                arch = Path("/home/flori/session-archive")
                arch.mkdir(exist_ok=True)
                cap = tmux(sock, "capture-pane", "-p", "-S", "-3000", "-t", name)
                (arch / f"{name}-{time.strftime('%F-%H%M')}.txt").write_text(cap.stdout or "")
                r = tmux(sock, "kill-session", "-t", name)
                return self._send(200, json.dumps({"ok": r.returncode == 0,
                                                   "archived_lines": len((cap.stdout or "").splitlines())}))
            m = re.fullmatch(r"pid:(\d+)", sid)
            if m:
                pid = int(m.group(1))
                if pid <= 1:
                    return self._send(400, json.dumps({"error": "refusing pid<=1"}))
                try:
                    os.kill(pid, 15)
                    return self._send(200, json.dumps({"ok": True}))
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)[:200]}))
            return self._send(400, json.dumps({"error": "unknown id"}))

        if path == "/api/prompt":
            sid, text = str(body.get("id", "")), str(body.get("text", ""))
            m = re.fullmatch(r"tmux:([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)", sid)
            if not m or not text.strip():
                return self._send(400, json.dumps({"error": "id or text missing"}))
            sock, name = m.groups()
            tmux(sock, "send-keys", "-t", name, "-l", text)
            time.sleep(0.4)
            r = tmux(sock, "send-keys", "-t", name, "Enter")
            return self._send(200, json.dumps({"ok": r.returncode == 0}))

        return self._send(404, json.dumps({"error": "not found"}))

if __name__ == "__main__":
    print(f"Agentenzentrale auf :{PORT}, Token {'gesetzt' if TOKEN else 'OFFEN'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
