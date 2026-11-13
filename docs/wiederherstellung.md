# Datensicherung und Wiederherstellung

## Was gesichert wird

Die App sichert ihre Datenbank (Artikel, Benutzer, Einstellungen, Protokolle) in den Ordner `/data/backups` im Container. Das ist das Volume `makerhub-data`, es bleibt bei einem Update erhalten.

| Art | Wann | Wie lange aufgehoben |
| --- | --- | --- |
| Nächtlich | jede Nacht um 3 Uhr (eingestellte Zeitzone) | die letzten 14 Tage, dazu die letzte Sicherung jedes Monats (12 Monate) |
| Von Hand | Knopf „Jetzt sichern“ unter Einstellungen → Sicherung | wie die nächtlichen |
| Vor Update (Migration) | automatisch beim Start, wenn die neue Version die Datenbank umbauen muss | die letzten 5 |

Die Dateien heißen z. B. `makerhub3d-20261113-030000-daily.db` (Zeit in UTC; Art: `daily`, `manual` oder `premigration`). Sie sind nur für den App-Benutzer lesbar und enthalten alle Daten, auch Passwort-Prüfwerte und verschlüsselte Zwei-Faktor-Geheimnisse. Bewahre Kopien nur an sicheren Orten auf.

Schlägt die Sicherung vor einem Update fehl, startet die App nicht (damit nichts verloren geht); der Grund steht im Protokoll: `docker compose logs app`.

## Sicherung herunterladen

Als Admin: Einstellungen → Sicherung → Dateiname anklicken. Lege Kopien regelmäßig außerhalb des Servers ab, denn eine Sicherung auf demselben Server schützt nicht vor einem Ausfall des Servers.

## Vor einem Update

1. Unter Einstellungen → Sicherung auf „Jetzt sichern“ klicken und prüfen, dass die neue Datei in der Liste steht (optional herunterladen).
2. Dann aktualisieren (siehe `docs/image-starten.md`). Beim Start legt die App zusätzlich eine Sicherung „Vor Update (Migration)“ an, falls die Datenbank umgebaut wird.

## Wiederherstellen

Eine Sicherung passt zu der Programmversion, mit der sie angelegt wurde, und zu **neueren** Versionen (die App baut die Datenbank beim Start selbst um). Sie passt **nicht** zu einer älteren Version.

Ein Update ist schiefgegangen und du willst zurück:

- Nimm die Sicherung „Vor Update (Migration)“ (oder die vom Knopf kurz vor dem Update).
- Starte dazu auch wieder die **alte** Programmversion (siehe `docs/image-starten.md`, „Eine bestimmte Version starten“).

Schritte (auf dem Server, im Ordner mit der `compose.yml`; `DATEI` ist der Name der Sicherung):

1. App anhalten:

   ```
   docker compose stop
   ```

2. Die Sicherung an die Stelle der Datenbank kopieren. Ein Hilfs-Container greift dafür auf dasselbe Volume zu; die Datenbank wird dabei ersetzt, deshalb vorher unbedingt prüfen, dass `DATEI` stimmt:

   ```
   docker compose run --rm --no-deps --entrypoint sh app -c \
     'cp /data/backups/DATEI /data/makerhub3d.db && rm -f /data/makerhub3d.db-wal /data/makerhub3d.db-shm'
   ```

   Das Volume wird dabei über die `compose.yml` automatisch eingehängt; du musst keinen Volume-Namen angeben. Hast du die Sicherung nur heruntergeladen und liegt sie nicht mehr im Ordner `/data/backups`, kopiere sie zuerst dorthin, z. B. mit `docker compose cp` (bei laufendem oder gestopptem Dienst) oder über einen Hilfs-Container.

3. App starten:

   ```
   docker compose up -d
   ```

4. Prüfen: `docker compose ps` zeigt „healthy“, und du kannst dich anmelden. Die Daten entsprechen dem Stand der Sicherung; alles danach Eingegebene fehlt.

Der Ablauf (Sicherung anlegen, Daten ändern, Datei zurückkopieren, App starten) wird in `tests/test_backup.py` automatisch geprüft.

Wichtig: Der Schlüssel `MAKERHUB_2FA_KEY` in der `.env` muss derselbe sein wie zur Sicherungszeit, sonst startet die App nicht.
