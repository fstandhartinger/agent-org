# Agentenzentrale

Zeigt und steuert alles, was auf dem Sandy-Server als Agent laeuft: tmux-Sitzungen
ueber alle Sockets, cron-Jobs, Hermes' eigene Jobs und die laufenden Prozesse von
Claude, codex, opencode und Hermes.

**Das Organigramm wird gemessen, nicht gepflegt.** Abteilung und Rolle ergeben sich
aus Regeln in `lib/collect.py`; was tatsaechlich laeuft, kommt live aus `tmux`,
`ps`, der crontab und `~/.hermes/cron/jobs.json`. Dadurch kann das Bild nicht
veralten — ein handgepflegtes Diagramm waere nach einem Tag falsch.

## Aufbau

    Host (systemd: agent-org.service, 0.0.0.0:8899)
      └── server.py + lib/collect.py     sieht tmux, ps, crontab
    Container (Coolify, nginx)
      └── reicht nach 172.17.0.1:8899 durch, besorgt Domain und TLS

Der Dienst laeuft bewusst NICHT im Container: tmux-Sockets, Prozessliste und
crontab des Hosts sind von innen nicht sichtbar.

## Zugang
`ORG_TOKEN` in `/etc/agent-org.env`. Ohne Token antwortet nur `/healthz`.
Die Oberflaeche kann Sitzungen beenden — sie darf nicht offen im Netz stehen.

## Was die Oberflaeche kann
- Baum nach Abteilungen, Subagenten eingerueckt unter ihrem Elternteil
- Badges: LÄUFT, CRON mit Intervall, STARTET SUBAGENTEN, INFRASTRUKTUR
- Laufzeit, Speicher, Tokenverbrauch
- Sitzung beenden (Verlauf wird vorher nach `~/session-archive/` gesichert)
- Nachricht an eine laufende Sitzung schicken
