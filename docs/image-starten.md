# Eine Version auf dem Server starten und zurückgehen

Bei jedem Merge auf `main` wird das Image automatisch gebaut, geprüft (Trivy) und nach ghcr.io (die Paketablage von GitHub) hochgeladen. Es bekommt dabei zwei Namensschilder (Tags):

- `latest`: immer die neueste Version
- ein kurzer Code aus 7 Zeichen, z. B. `a1b2c3d`: genau diese eine Version, die sich nie ändert

Den Code findest du auf GitHub unter „Commits“ (die ersten 7 Zeichen der Commit-Kennung) oder beim Paket unter „Packages“ in der Liste der Versionen.

In den Beispielen steht `OWNER/REPO` für den Namen des Repositorys auf GitHub, komplett klein geschrieben (z. B. `meinname/makerhub3d`).

**Einmalig:** Das Paket muss öffentlich sein, sonst verlangt der Server eine Anmeldung. Das stellst du auf GitHub unter dem Paket in den Einstellungen ein („Change visibility“ → Public).

## Eine bestimmte Version starten

1. Auf dem Server in den Ordner mit der `compose.yml` wechseln.
2. In der Datei `.env` diese Zeile eintragen (oder ändern), mit dem gewünschten Code:

   ```
   MAKERHUB_IMAGE=ghcr.io/OWNER/REPO:a1b2c3d
   ```

3. Version herunterladen und starten:

   ```
   docker compose pull
   docker compose up -d --no-build
   ```

4. Prüfen, ob alles läuft: `docker compose ps` sollte „healthy“ anzeigen.

Die Daten (Datenbank) liegen getrennt im Speicherbereich `/data` und bleiben beim Wechsel der Version erhalten.

## Auf die neueste Version gehen

In der `.env` den Tag auf `latest` setzen (`MAKERHUB_IMAGE=ghcr.io/OWNER/REPO:latest`) und die Schritte 3 und 4 wiederholen.

## Zurück zu einer älteren Version

1. In der `.env` den Code der Version eintragen, die vorher gut lief.
2. Schritte 3 und 4 wiederholen.

Hinweis: Sobald es eine Datenbank gibt, kann eine neue Version deren Aufbau verändern (Migration). Mache **vor jedem Versionswechsel eine Sicherung** der Daten, denn eine ältere Version kommt mit einer schon umgebauten Datenbank eventuell nicht zurecht. Die Anleitung zur Sicherung kommt mit der Aufgabe „Datensicherung“.

## Wieder lokal bauen (Entwicklung)

Die Zeile `MAKERHUB_IMAGE` aus der `.env` entfernen oder auskommentieren und `docker compose up --build` ausführen.
