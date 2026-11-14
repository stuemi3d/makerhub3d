# CLAUDE.md – Regeln für die Arbeit an MakerHub 3D

Diese Datei gilt für jede Claude-Code-Sitzung in diesem Repository. Der vollständige Plan steht in `docs/plan.md` – lies ihn, bevor du eine Aufgabe beginnst, und halte dich an ihn. Weicht eine Aufgabe vom Plan ab oder ist etwas unklar: **nachfragen, nicht raten.**

## Rollen

- **Maintainer:** der Mensch, der dieses Repo besitzt. Er entscheidet, gibt Testfälle frei, prüft Pull Requests und merged. Er programmiert nicht selbst und kann Code nicht Zeile für Zeile prüfen – schreibe deshalb alles, was er beurteilen soll, in verständlichem Deutsch.
- **Du (Claude Code):** setzt Aufgaben um, immer eine nach der anderen.

## Sprache

- Kommunikation, Issues, Pull Requests, Commit-Nachrichten: **Deutsch**.
- Code, Bezeichner, Kommentare im Code: **Englisch**.
- Oberflächentexte: nie fest im Code, sondern über die Übersetzungsdateien (Deutsch Standard).
- **Rückfragen an den Maintainer** (im Chat, in Issues und PRs) schreibst du so, dass er sie mit „OK“ oder einer kurzen Antwort entscheiden kann:
  - in einfachen Worten, ohne Fachbegriffe (oder mit kurzer Erklärung in Klammern)
  - pro Frage: worum es geht, deine Empfehlung, und was passiert, wenn er „OK“ sagt

## Ablauf pro Aufgabe (verbindlich)

1. Aufgabe = ein GitHub-Issue. Lies Issue und `docs/plan.md`.
2. **Testfälle in Klartext** als Kommentar ins Issue schreiben (z. B. „Wenn ich einen Druck mit 10 guten Teilen anbuche, steigt der Bestand um 10“). Dann **auf die Bestätigung des Maintainers warten**, bevor du programmierst.
3. Branch anlegen: `aufgabe/<issue-nr>-<kurzname>` (von aktuellem `main`).
4. Zuerst die Tests schreiben, dann den Code.
5. Lokal muss alles grün sein: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`. Hat sich eine Seite oder Oberfläche geändert, zusätzlich die Browser-Tests (siehe „Browser-Tests“).
6. **Unabhängiges Review:** Starte einen Subagenten mit frischem Kontext, der den Diff gegen diese Datei und die Sicherheits-Checkliste unten prüft. Der Subagent läuft mit dem Modell **Sonnet** (spart Nutzungslimit). Behebe, was er findet, oder begründe, warum nicht.
7. Commit(s), Push des Branches, Pull Request mit der Vorlage (`.github/pull_request_template.md`), verknüpft mit dem Issue (`Closes #<nr>`).
8. Kommentare des Maintainers im PR umsetzen, bis er zufrieden ist. **Er merged – nie du.**

**Ausnahme:** Reine Doku- oder Organisationsaufgaben ohne Programmcode (z. B. Plan, Regeln, Vorlagen, Issues) brauchen keine test-bezogenen Schritte – Schritt 2 (Testfall-Freigabe), Schritt 4 (Tests zuerst schreiben) und Schritt 5 (Testlauf) entfallen dann. Unter „Testfälle“ steht im PR, dass es keinen Code und deshalb keine automatisierten Tests gibt. Das unabhängige Review (Schritt 6) läuft immer.

**Schnellspur für reine Oberflächen-Änderungen:** Sie gilt nur, wenn **alle** Punkte zutreffen. Im Zweifel gilt der volle Ablauf.

- Die Änderung betrifft nur das Aussehen: CSS, Abstände, Farben, Größen, Anordnung und Texte. Texte stehen ausschließlich in den Übersetzungsdateien, ohne HTML-Markup. Texte zu Löschen, Rechten oder Sicherheit sind ausgenommen.
- In Templates (`*.html`) dürfen nur CSS-Klassen, Attribute fürs Aussehen und statische Elemente ohne Funktion geändert oder verschoben werden. **Nicht erlaubt:** Änderungen an `<form>`, `<input>`, `<button>`, `hx-*`-Attributen, `csrf_token`, Jinja-Anweisungen (`{% %}`) und Ausdrücken (`{{ }}`), Verschieben von Blöcken in oder aus Rollen-Bedingungen, `|safe`, neue externe Ressourcen (Schrift, CDN, Skript).
- Es ändert sich außerdem nichts an Routen, Rechten und Login, Datenbank und Modellen, Berechnungen, Uploads, Abhängigkeiten und `.github/workflows/`.
- Der Maintainer hat die Änderungen ausdrücklich gewünscht (Chat oder Issue). Mehrere Wünsche dürfen in einem Sammel-Issue und einem Pull Request stehen, solange der Pull Request klein bleibt; sonst aufteilen.

In der Schnellspur gilt:

- Du prüfst vor dem Commit selbst, ob alle Punkte zutreffen. Fällt dir beim Umsetzen etwas Nichtoptisches auf, stoppst du und wechselst für diesen Teil in den vollen Ablauf (Testfälle im Issue, Freigabe abwarten) oder trennst ihn in einen eigenen Pull Request ab.
- Schritt 2 entfällt: keine Testfall-Freigabe vorab. Das Issue nennt nur die gewünschten Änderungen.
- Schritt 4 entfällt, außer ein Browser-Test wird angepasst oder ergänzt (dann zuerst der Test, dann die Änderung). Das ist nötig, wenn Layout oder Bedienung (z. B. Handy-Ansicht) an der Änderung hängt. Schritt 5 bleibt vollständig: `uv run pytest`, `ruff` und die Browser-Tests müssen grün sein.
- Das Review (Schritt 6) läuft wie üblich einmal pro Pull Request, auch wenn er mehrere Wünsche bündelt, mit der vollständigen Sicherheits-Checkliste. Zusätzlich prüft der Subagent, ob der Diff nur Erlaubtes enthält; findet er Logik, Formulare oder Rechte, werden diese Teile abgetrennt und im vollen Ablauf umgesetzt.
- Im Pull Request steht „Schnellspur“, dazu eine Liste der geänderten Dateien nach Art (CSS, Template, Übersetzung). Unter „Testfälle“ steht, dass nur bestehende Tests laufen (angepasste oder ergänzte Browser-Tests werden genannt). Unter „Achtung“ steht „Keine heiklen Änderungen.“
- Merge weiterhin nur durch den Maintainer.

Erledigt ein Pull Request einen Punkt der Meilensteine in `docs/plan.md` (Abschnitt 10), hakst du ihn dort im selben PR ab (`[x]`).

Halte jeden Pull Request klein. Wird eine Aufgabe größer als gedacht: stoppen, Vorschlag zur Aufteilung machen.

## Browser-Tests (Playwright)

- Browser-Tests liegen in `tests/browser/` und laufen getrennt vom normalen Testlauf: `uv run pytest -m browser`. Jeder Test läuft in Desktop- und Handy-Ansicht (390 px breit).
- Der Test-Browser ruft nur die lokale Testversion auf (`127.0.0.1`, eigene leere Datenbank in einem temporären Ordner). Jede Anfrage an eine andere Adresse wird blockiert und lässt den Test fehlschlagen.
- Chromium wird einmalig im Home-Ordner installiert, ohne `sudo`: `uv run playwright install chromium`. Fehlen Systembibliotheken, nenne dem Maintainer den genauen `sudo`-Befehl und erkläre in einfachen Worten, was er installiert.
- Bei einem fehlgeschlagenen Browser-Test liegt ein Screenshot in `test-results/` (nicht im Git).
- **Sparsam mit dem Nutzungslimit:** Schau dir Screenshots nur an, wenn sich die Oberfläche geändert hat oder ein Browser-Test fehlschlägt – nicht routinemäßig.
- Neue oder geänderte Seiten bekommen einen Browser-Test, wenn Layout oder Bedienung (z. B. Handy-Ansicht) davon abhängen. In GitHub Actions läuft die Prüfung „Browser-Tests“ bei jedem Pull Request.

## Was du nie tust

- Nicht mergen, keine PRs freigeben, nicht auf `main` pushen, kein Force-Push, keine Repo-Einstellungen ändern.
- Keine Passwörter, Tokens, Keys, IP-Adressen, Hostnamen, E-Mail-Adressen oder sonstige persönliche Daten in Code, Tests, Fixtures, Commits, Issues oder PRs.
  - **Überall erlaubt** (Code, Tests, Doku, Konfiguration): `127.0.0.1`, `localhost` und Beispiel-Domains nach RFC 2606 (`example.com`, `example.org`, `*.example`).
  - **`0.0.0.0`** ist nur in Container- und Server-Konfiguration erlaubt.
  - **Beispieldaten sind immer erfunden.** Es gibt genau zwei Ausnahmen; bei beiden weist du im PR unter „Achtung“ darauf hin:
    - **3MF- und G-Code-Testdateien** stellt der Maintainer bereit (selbst gesliced, z. B. ein Würfel). Bevor sie ins Repo kommen, entfernst du Benutzernamen, Seriennummern, Pfade und ähnliche Metadaten.
    - **Aufgezeichnete Bambuddy-Antworten** werden echt aufgezeichnet und dann bereinigt: Druckernamen, Seriennummern, Dateinamen, Benutzernamen, IP-Adressen/Hostnamen und andere persönliche Werte ersetzt du durch erfundene.
- Keine `.env`-Dateien lesen außer `.env.example` und `.env.test`. `.env.test` bleibt lokal und kommt nie ins Repo (dort steht später der Bambuddy-Test-Key). Echte Zugangsdaten gehören nie ins Repo; im Repo liegt nur `.env.example` mit Platzhaltern.
- Keine Dateien außerhalb dieses Repos ändern (Ausnahme: temporäre Dateien in `/tmp`).
- Nichts deployen und keine laufende Instanz verändern. Das macht der Maintainer.
- Keine Netzwerkzugriffe außer Paketquellen, GitHub, dem einmaligen Chromium-Download von Playwright und – nur wenn eine Aufgabe es verlangt – der Bambuddy-Test-API.
- Keine Abhängigkeit hinzufügen, ohne sie in der PR-Beschreibung unter „Achtung“ zu nennen und zu begründen.

## Pull-Request-Beschreibung

Jeder PR enthält (siehe Vorlage):

- **Was ändert sich** – in einfachen Worten, ohne Fachjargon
- **So testest du es** – Schritte zum Ausprobieren
- **Testfälle** – die bestätigten Klartext-Testfälle und dass sie automatisiert geprüft werden
- **Achtung** – ausdrücklich auflisten, falls zutreffend: neue/aktualisierte Abhängigkeit, Datenbank-Migration, Änderung an `.github/workflows/`, Login/Rechte/Sicherheit, Datei-Uploads, neue Testdaten aus echten Quellen (3MF/G-Code, aufgezeichnete Bambuddy-Antworten) mit entfernten bzw. ersetzten persönlichen Werten, Zugriff auf Bambuddy oder andere externe Dienste. Wenn nichts davon zutrifft: „Keine heiklen Änderungen.“
- **Review-Ergebnis** – was der unabhängige Review-Durchlauf gefunden hat und wie es behandelt wurde

## Technik und Konventionen

- Python 3.12, Abhängigkeiten mit **uv** (`pyproject.toml` + `uv.lock`, feste Versionen).
- FastAPI, Jinja2 + HTMX, Pico CSS, Chart.js; SQLAlchemy 2 + Alembic; httpx; pytest; ruff.
- Struktur: `app/` (Code), `tests/` (Tests), `docs/` (Doku), `migrations/` (Alembic).
- Geldbeträge mit `Decimal`, nie mit `float`. Gramm und Stunden ebenfalls `Decimal`.
- Kosten und Preise beim Buchen als Wert speichern; Bestand immer aus Lagerbewegungen berechnen.
- Produktionen und Lagerbewegungen nie löschen, sondern stornieren. **Einzige Ausnahme:** Ein Admin darf einen Artikel „endgültig löschen“ (samt Kalkulationen, Lagerbewegungen, Verkäufen und der Produktion bzw. bei mehreren Ergebnis-Artikeln nur seiner Zeile darin), mit Warnung, Bestätigung durch Abtippen der Artikelnummer und Eintrag im Änderungsprotokoll. Löschregeln für Artikel und Vorlagen: siehe `docs/plan.md`, Abschnitt 4.
- Jede Datenbankänderung über eine Alembic-Migration, mit Test.
- Bambuddy-Zugriffe ausschließlich über einen einzigen Client-Baustein; Tests nutzen aufgezeichnete, bereinigte Antworten (Fixtures, siehe „Was du nie tust“), nie die echte API.
- Konfiguration nur über Umgebungsvariablen (siehe `.env.example`).

## Sicherheits-Checkliste (für jedes Review)

- [ ] Keine Geheimnisse oder persönlichen Daten im Diff
- [ ] Jede Seite/Route erfordert Login und prüft die Rolle
- [ ] Formulare mit CSRF-Schutz; Eingaben validiert
- [ ] Keine rohen SQL-Strings mit Benutzereingaben
- [ ] Templates escapen Ausgaben (kein `|safe` ohne Begründung)
- [ ] Uploads: Größenlimit, Typprüfung, kein Entpacken außerhalb eines temporären Ordners, Pfade nicht aus Dateinamen übernehmen
- [ ] Keine neuen ausgehenden Verbindungen außer den vorgesehenen
- [ ] Fehler geben keine internen Details oder Keys preis
- [ ] Neue Abhängigkeiten sind verbreitet, gepflegt und begründet
