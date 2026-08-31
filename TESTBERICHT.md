# Testbericht Agent Org — 31.08.2026

Geprüft wurde die ausgerollte Fassung auf <https://agents.app.mintapis.com>, nicht
der lokale Quelltext. Der Browser war ein echter Chromium (Playwright 1.62.1,
headless); das Token stand vor dem ersten Laden per `addInitScript` im
`localStorage` unter `orgtok`. Alle Zahlen in diesem Bericht sind gemessen, nicht
geschätzt — das Prüfskript liegt unter `.screenshots/run.js` (nicht im Repo,
`.screenshots/` ist gitignored), die Messwerte unter `.screenshots/report.json`.

Stand der Daten zum Zeitpunkt der Aufnahmen: 24 Einheiten, 52 Prozesse davon 41
Subagenten.

---

## 1. Was kaputt war

### 1.1 Das Terminalfenster war immer sichtbar (der gemeldete Fehler)

Bestätigt. `#term` trug korrekt das `hidden`-Attribut, war aber trotzdem
sichtbar. Grund: die Standardregel des Browsers `[hidden]{display:none}` hat die
Spezifität 0,1,0 und verliert gegen `#term{…;display:flex}` mit 1,0,0. Das Panel
lag mit `position:fixed;height:62vh` fest über der unteren Bildschirmhälfte.

**Behoben** durch eine globale Regel direkt nach dem Reset:

```css
[hidden]{display:none!important}
```

Bewusst global und nicht nur `#term[hidden]`: über `hidden` gesteuert werden vier
Container — `#gate`, `#app`, `#chart`, `#term`. Von denen setzte nur `#term` ein
`display`, die anderen drei waren zum Prüfzeitpunkt in Ordnung. Die globale Regel
sichert sie alle gegen dieselbe Falle ab, falls später jemand eine ID-Regel mit
`display` ergänzt.

Belegt: im Startzustand ist `#term` sowohl auf Desktop als auch im Hochformat
`display: none`, Bounding-Box 0×0 (`report.json` → `desktopStart.term`,
`mobileStart.term`).

### 1.2 Das Organigramm war 5203 px breit und 350 px hoch (Nachtrag Florian)

Bestätigt und gemessen. `chartFor()` legte jede Ebene in ein `.hwrap`, das alle
Kinder nebeneinander stellt. Bei 6 Abteilungen mit zusammen 22 Einheiten ergab
das:

| | vorher | nachher |
|---|---|---|
| `#chart.scrollWidth` Desktop 1440 | **5203 px** | **1156 px** |
| `#chart.clientWidth` Desktop 1440 | 1156 px | 1156 px |
| `#chart` Höhe Desktop | 350 px | 919 px |
| `#chart.scrollWidth` Hochformat 390 | **5190 px** | **366 px** |
| `#chart.clientWidth` Hochformat 390 | 366 px | 366 px |

Waagerechtes Scrollen ist damit auf beiden Größen vollständig entfallen
(`scrollWidth == clientWidth`).

**Neue Form** — im Wesentlichen Florians Vorschlag, ohne Änderung am Konzept:

- Florian oben als einzelner Kasten, darunter eine senkrechte Linie.
- Darunter ein Grid `repeat(auto-fit, minmax(184px, 1fr))`. Auf 1440 px ergibt
  das fünf Abteilungsspalten nebeneinander (Research war leer), im Hochformat
  eine.
- In jeder Spalte die Einheiten untereinander an einer durchgehenden Senkrechten
  mit kurzem waagerechtem Stummel zu jedem Kasten. Die letzte Einheit bekommt
  einen Winkel statt einer durchlaufenden Linie.
- Subagenten hängen eingerückt in derselben Spalte unter ihrer Einheit.

Aus „22 nebeneinander" wurde „5 nebeneinander, je 2–7 untereinander".

Eine Abweichung von der Vorgabe, mit Begründung: die **waagerechte
Sammelschiene** zwischen Florian und den Spalten lässt sich nicht rein in CSS
korrekt zeichnen, weil erst die Layout-Engine entscheidet, wo das Grid umbricht.
Sie wird deshalb aus je einem Segment pro Spalte gebaut (erste Spalte ab der
Mitte, letzte bis zur Mitte, dazwischen volle Breite), und `busify()` misst nach
jedem Rendern und bei `resize` per `offsetTop`, welche Spalten in derselben Reihe
sitzen. Spalten in einer umgebrochenen zweiten Reihe bekommen gar keine Linie
(`.col.norail`) statt einer, die ins Leere zeigt. Gemessene Klassen im
Hochformat: `['col alone | Leadership', 'col norail | Production', 'col norail |
Distribution', 'col norail | Security', 'col norail | Operations']` — die erste
Abteilung hängt sichtbar an Florian, die übrigen stehen als saubere Abschnitte
darunter.

### 1.3 Was mir erst beim Ansehen der Screenshots aufgefallen ist

Diese vier Punkte standen in keinem Auftrag; sie sind auf den Bildern der ersten
Runde sichtbar gewesen:

1. **Badges ragten seitlich aus den Kästen.** `.badge` hatte
   `white-space:nowrap`; `SPAWNS · few · via opencode/codex` ist breiter als eine
   184-px-Spalte und stand deutlich sichtbar über dem Rand von
   `gmail-inbox-watch`. Im Diagramm dürfen Badges jetzt umbrechen. Weil eine
   zweizeilige Pille mit `border-radius:20px` aufgerissen aussieht, sind sie dort
   auf 5 px Radius umgestellt — sie lesen sich jetzt als ein Element.
2. **Rahmen mit nur zwei Abteilungen streckten die Kästen.** Im Modus „System
   boundaries" hat der Laptop-Rahmen nur zwei Spalten; die Kästen wurden über
   500 px breit bei drei Zeilen Inhalt. Der Spalteninhalt ist ab 601 px Viewport
   auf 300 px gedeckelt und zentriert; die Spalte selbst bleibt breit, damit die
   Sammelschiene weiter an den Spaltenmitten hängt.
3. **Das Terminal zerschnitt Wörter.** `white-space:pre-wrap` zusammen mit
   `word-break:break-word` ergab „New sessi/ons". `word-break` ist raus,
   `overflow-wrap:break-word` bricht nur noch, wenn ein Wort allein nicht in die
   Zeile passt.
4. **Im Hochformat brach der Sitzungsname im Terminalkopf um.** Name und
   Untertitel sind jetzt einzeilig mit Ellipse, der Untertitel entfällt unter
   520 px.

---

## 2. Die sechs geforderten Prüfpunkte

Alle sechs sind sauber. Jeweils mit dem Screenshot, der es belegt.

| # | Prüfpunkt | Ergebnis | Screenshot |
|---|---|---|---|
| 1 | Desktop 1440×1000, Startzustand: Terminal unsichtbar | **ok** — `#term` `display:none`, Rect 0×0 | `01-desktop-start.png` |
| 2 | Hochformat 390×844, Startzustand: Terminal unsichtbar | **ok** — dito | `02-mobile-start.png` |
| 3 | „Org chart" sieht wie ein Organigramm aus, Linien da, nichts überlappt | **ok** | `03-desktop-orgchart.png`, `02c2-mobile-orgchart-full.png` |
| 4 | „Show subagents" zeigt Prozesse | **ok** — 41 `SUBAGENT`-Badges, Abschnitt „Live processes · 52 (41 subagents)" | `04-desktop-subagents-chart.png`, `04b-desktop-subagents-list.png` |
| 5 | „System boundaries" zeigt zwei Rahmen | **ok** — „Sandy · Hetzner server · 22" und „Florian's laptop · 2" | `05-desktop-hosts-list.png`, `05b-desktop-hosts-chart.png` |
| 6 | „Remote control" öffnet ein Terminal mit Text, lässt sich schließen | **ok** | `06-desktop-terminal-open.png`, `06b-desktop-terminal-closed.png` |

Zu Punkt 3 im Detail: die Linien wurden nicht nur angesehen, sondern gemessen.
Jede Spalte hat einen Stummel von 2×14 px in `rgb(37,44,55)` nach oben, die
Sammelschiene ist 2 px hoch und läuft bei der ersten Spalte ab `left:115px`
(Spaltenmitte bei 230 px Breite), bei den mittleren über die volle Spaltenbreite
und endet bei der letzten wieder in der Mitte. Auf Überlappung geprüft wurde
paarweise über alle `.node`-Kästen: leer. Auf abgeschnittenen Text geprüft wurde
über `scrollWidth/scrollHeight` gegen `clientWidth/clientHeight` bei allen
Kästen, Titeln, Rollen und Badges: leer, auf Desktop wie im Hochformat.
`document.documentElement.scrollWidth` überschreitet in keiner Ansicht die
Fensterbreite. Konsolenfehler beim Laden: keine.

Zu Punkt 6 im Detail: geklickt wurde am ersten „Remote control" (Karte
`claude-rc`). Danach `#term` `display:flex`, Rect 1440×620 am unteren Rand,
Kopfzeile „claude-rc / Remote-control hub", im Pane 241 Zeilen echter
tmux-Ausgabe (`scrollHeight` 5952 px gegen 513 px sichtbar, also scrollbar). Nach
Klick auf „Close" wieder `display:none`. Im Hochformat derselbe Ablauf, Panel
390×523 (`02d-mobile-terminal.png`).

---

## 3. Was noch offen ist

Ehrlich, in der Reihenfolge der Wichtigkeit:

1. **Der Terminalinhalt bleibt hässlich, und das lässt sich nicht ganz
   wegräumen.** Eine `tmux capture-pane`-Zeile ist rund 200 Zeichen breit, der
   Pane fasst bei 1440 px etwa 195. Jede Zeile bricht also mindestens einmal um,
   und weil `pre-wrap` die Leerzeichenblöcke der tmux-Statuszeile erhält, wird
   gelegentlich immer noch ein Wort geteilt (auf `06-desktop-terminal-open.png`
   sichtbar bei „New sessi/ons"). Ich habe die Alternative `white-space:pre` mit
   waagerechtem Scrollen ausprobiert und wieder verworfen: auf dem 390-px-Schirm
   war dann fast nichts mehr ohne Schieben lesbar (Zwischenstand im Commit
   `8fd874a`, zurückgenommen in `af8dc82`). Lesbarkeit auf dem Handy hat für mich
   schwerer gewogen als saubere Wortgrenzen auf dem Desktop.
2. **Der Pane von `claude-rc` zeigt gerade fast nur Statuszeilen** („Capacity:
   4/32 · New sessions will be created in the current directory · Connected ·
   flori · HEAD", dazu „Reconnecting · retrying in 2.4s"). Das ist der echte
   Inhalt der Sitzung, kein Anzeigefehler — aber wer die Fernsteuerung zum ersten
   Mal öffnet, sieht dadurch nichts Nützliches.
3. **`claude remote-control` erscheint doppelt.** In der Prozessliste steht es
   einmal als Wurzelprozess und noch einmal als eigener `SUBAGENT` mit pid 292603
   und „4 children" (`04b`, Abschnitt „Live processes"). Das kommt aus
   `lib/collect.py`, nicht aus der Oberfläche, und ich habe es nicht angefasst.
4. **Alle 41 Subagenten hängen unter `claude-rc`.** `chartFor()` hängt sie an die
   erste Einheit, deren Rolle auf `/Remote-control hub/i` passt. Dadurch wird im
   Modus „Show subagents" die Spalte Leadership sehr lang, während die anderen
   kurz bleiben. Das ist vorbestehendes Verhalten aus dem Datenmodell, das ich
   bewusst nicht mit der Layout-Änderung vermischt habe.
5. **Auf mittleren Breiten (etwa 700–1000 px) bricht das Spaltengrid in zwei
   Reihen um.** Die zweite Reihe bekommt dann bewusst keine Verbindungslinie nach
   oben. Das ist korrekt im Sinne von „lieber keine Linie als eine falsche", aber
   es ist ein Kompromiss und kein schönes Organigramm. Auf den beiden geforderten
   Größen (1440 und 390) tritt der Fall nicht auf.
6. **Der Modus „System boundaries" zeichnet Florian zweimal** — je einmal an der
   Spitze des Sandy-Rahmens und des Laptop-Rahmens (`05b`). Sachlich richtig,
   aber es liest sich, als gäbe es zwei Florians. Nicht geändert, weil es eine
   inhaltliche Entscheidung ist.
7. **Nicht geprüft:** „Kill" und „Send" wurden bewusst nicht ausgelöst — beides
   greift in laufende Sitzungen ein. Der Ratenbegrenzer-Zustand („Live: off") und
   das Token-Gate (`#gate`) wurden ebenfalls nicht durchgespielt.

---

## 4. Die Änderungen

Alles in `static/index.html`, ausgerollt auf Sandy (`systemctl restart
agent-org`) und danach jeweils gegen die öffentliche URL nachgemessen.

| Commit | Inhalt |
|---|---|
| `f3f0b67` | `[hidden]{display:none!important}` — der gemeldete Fehler |
| `4ee6cf8` | Organigramm als Abteilungsspalten, `busify()`, Badges dürfen umbrechen |
| `8fd874a` | Badge-Radius, Deckelung der Spaltenbreite, Terminaltypografie, Kopfzeile im Hochformat |
| `af8dc82` | Terminal wieder umbrechend, aber ohne `word-break` |
