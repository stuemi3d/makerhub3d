# CLAUDE.md – Regeln für die Arbeit an MakerHub 3D

Diese Datei gilt für jede Claude-Code-Sitzung in diesem Repository. Der vollständige Plan steht in `docs/plan.md` – lies ihn, bevor du eine Aufgabe beginnst, und halte dich an ihn. Weicht eine Aufgabe vom Plan ab oder ist etwas unklar: **nachfragen, nicht raten.**

## Rollen

- **Maintainer:** der Mensch, der dieses Repo besitzt. Er entscheidet, gibt Testfälle frei, prüft Pull Requests und merged. Er programmiert nicht selbst und kann Code nicht Zeile für Zeile prüfen – schreibe deshalb alles, was er beurteilen soll, in verständlichem Deutsch.
- **Du (Claude Code):** setzt Aufgaben um, immer eine nach der anderen.

## Sprache

- Kommunikation, Issues, Pull Requests, Commit-Nachrichten: **Deutsch**.
- Code, Bezeichner, Kommentare im Code: **Englisch**.
- Oberflächentexte: nie fest im Code, sondern über die Übersetzungsdateien (Deutsch Standard).

## Ablauf pro Aufgabe (verbindlich)

1. Aufgabe = ein GitHub-Issue. Lies Issue und `docs/plan.md`.
2. **Testfälle in Klartext** als Kommentar ins Issue schreiben (z. B. „Wenn ich einen Druck mit 10 guten Teilen anbuche, steigt der Bestand um 10“). Dann **auf die Bestätigung des Maintainers warten**, bevor du programmierst.
3. Branch anlegen: `aufgabe/<issue-nr>-<kurzname>` (von aktuellem `main`).
4. Zuerst die Tests schreiben, dann den Code.
5. Lokal muss alles grün sein: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`.
6. **Unabhängiges Review:** Starte einen Subagenten mit frischem Kontext, der den Diff gegen diese Datei und die Sicherheits-Checkliste unten prüft. Behebe, was er findet, oder begründe, warum nicht.
7. Commit(s), Push des Branches, Pull Request mit der Vorlage (`.github/pull_request_template.md`), verknüpft mit dem Issue (`Closes #<nr>`).
8. Kommentare des Maintainers im PR umsetzen, bis er zufrieden ist. **Er merged – nie du.**

Halte jeden Pull Request klein. Wird eine Aufgabe größer als gedacht: stoppen, Vorschlag zur Aufteilung machen.

## Was du nie tust

- Nicht mergen, keine PRs freigeben, nicht auf `main` pushen, kein Force-Push, keine Repo-Einstellungen ändern.
- Keine Passwörter, Tokens, Keys, IP-Adressen, Hostnamen, E-Mail-Adressen oder sonstige persönliche Daten in Code, Tests, Fixtures, Commits, Issues oder PRs. Beispieldaten sind immer erfunden.
- Keine `.env`-Dateien lesen außer `.env.test`. Echte Zugangsdaten gehören nie ins Repo.
- Keine Dateien außerhalb dieses Repos ändern (Ausnahme: temporäre Dateien in `/tmp`).
- Nichts deployen und keine laufende Instanz verändern. Das macht der Maintainer.
- Keine Netzwerkzugriffe außer Paketquellen, GitHub und – nur wenn eine Aufgabe es verlangt – der Bambuddy-Test-API.
- Keine Abhängigkeit hinzufügen, ohne sie in der PR-Beschreibung unter „Achtung“ zu nennen und zu begründen.

## Pull-Request-Beschreibung

Jeder PR enthält (siehe Vorlage):

- **Was ändert sich** – in einfachen Worten, ohne Fachjargon
- **So testest du es** – Schritte zum Ausprobieren
- **Testfälle** – die bestätigten Klartext-Testfälle und dass sie automatisiert geprüft werden
- **Achtung** – ausdrücklich auflisten, falls zutreffend: neue/aktualisierte Abhängigkeit, Datenbank-Migration, Änderung an `.github/workflows/`, Login/Rechte/Sicherheit, Datei-Uploads, Zugriff auf Bambuddy oder andere externe Dienste. Wenn nichts davon zutrifft: „Keine heiklen Änderungen.“
- **Review-Ergebnis** – was der unabhängige Review-Durchlauf gefunden hat und wie es behandelt wurde

## Technik und Konventionen

- Python 3.12, Abhängigkeiten mit **uv** (`pyproject.toml` + `uv.lock`, feste Versionen).
- FastAPI, Jinja2 + HTMX, Pico CSS, Chart.js; SQLAlchemy 2 + Alembic; httpx; pytest; ruff.
- Struktur: `app/` (Code), `tests/` (Tests), `docs/` (Doku), `migrations/` (Alembic).
- Geldbeträge mit `Decimal`, nie mit `float`. Gramm und Stunden ebenfalls `Decimal`.
- Kosten und Preise beim Buchen als Wert speichern; Bestand immer aus Lagerbewegungen berechnen.
- Produktionen und Lagerbewegungen nie löschen, sondern stornieren.
- Jede Datenbankänderung über eine Alembic-Migration, mit Test.
- Bambuddy-Zugriffe ausschließlich über einen einzigen Client-Baustein; Tests nutzen aufgezeichnete Antworten (Fixtures), nie die echte API.
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
