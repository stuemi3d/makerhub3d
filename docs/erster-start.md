# Erster Start und Anmeldung

MakerHub 3D hat kein Standardpasswort. Beim ersten Start legst du den ersten Admin selbst an.

## Ersten Admin anlegen

1. App starten (siehe [image-starten.md](image-starten.md)).
2. Im Container-Protokoll den **Einrichtungscode** suchen:

   ```
   docker compose logs app
   ```

   Dort steht eine Zeile wie „Einrichtungscode für den ersten Admin: a1b2-c3d4-e5f6-a7b8“.
3. MakerHub 3D im Browser öffnen. Solange es keinen Benutzer gibt, erscheint automatisch die Seite „Ersten Admin anlegen“.
4. Einrichtungscode, Benutzername und Passwort (mindestens 12 Zeichen, zweimal) eingeben. Danach bist du angemeldet.

Der Code ändert sich bei jedem Neustart, solange noch kein Admin angelegt ist. Sobald es einen Benutzer gibt, ist die Einrichtungsseite verschwunden und es wird kein Code mehr angezeigt.

Der Code verhindert, dass ein Fremder dir zuvorkommt, falls die App schon aus dem Internet erreichbar ist, bevor du den Admin angelegt hast.

## Anmeldung

- Nach **5 Fehlversuchen hintereinander** ist ein Konto **15 Minuten** gesperrt. Danach geht es wieder.
- Nach **8 Stunden ohne Aktivität** wirst du automatisch abgemeldet.
- Abmelden: Knopf „Abmelden“ oben rechts.

## HTTPS und Heimnetz

Das Anmelde-Cookie wird nur über HTTPS (verschlüsselte Verbindung) gesendet, also über den vorgeschalteten Reverse Proxy (z. B. Caddy). Direkt auf demselben Rechner über `http://localhost` geht es in den gängigen Browsern (Chrome, Firefox) trotzdem.

Rufst du MakerHub 3D **im eigenen Heimnetz ohne HTTPS** auf (z. B. über die Netzwerkadresse des Rechners), kannst du dich sonst nicht anmelden. Dann in der `.env` eintragen:

```
MAKERHUB_INSECURE_COOKIES=true
```

Das ist nur im eigenen Heimnetz vertretbar: Benutzername, Passwort und Anmeldung gehen dann unverschlüsselt durchs Netz. Nie verwenden, wenn die App aus dem Internet erreichbar ist.

## Eingebettet in Bambuddy

MakerHub 3D läuft immer auch für sich allein im Browser. Eingebettet in die Bambuddy-Seitenleiste klappt die Anmeldung nur, wenn

- die Adresse von Bambuddy in `MAKERHUB_EMBED_ORIGINS` steht und
- Bambuddy und MakerHub 3D unter **derselben Hauptadresse** laufen, z. B. `bambuddy.example.com` und `makerhub.example.com`.

Bei ganz verschiedenen Adressen schicken Browser das Anmelde-Cookie im eingebetteten Rahmen nicht mit. MakerHub 3D funktioniert dann nur in einem eigenen Browser-Tab.

## Weitere Benutzer

Unter **Einstellungen → Benutzer** (nur für Admins) legst du weitere Benutzer an. Jeder bekommt eine feste Rolle:

| Rolle | Darf |
| --- | --- |
| Admin | alles, auch Einstellungen und Benutzer |
| Mitarbeiter | ansehen und bearbeiten (Artikel, Kalkulation, Produktion, Lager, Verkäufe) |
| Nur lesen | nur ansehen |

- Das Startpasswort vergibst du selbst und teilst es dem Benutzer mit; es wird keine E-Mail verschickt. Der Benutzer muss es bei seiner ersten Anmeldung ändern. In der Benutzerliste siehst du, wer das noch nicht getan hat.
- Benutzer werden nie gelöscht, nur **deaktiviert**. Ein deaktivierter Benutzer wird sofort abgemeldet und kann sich nicht mehr anmelden. Du kannst ihn jederzeit wieder aktivieren.
- **Passwort zurücksetzen:** neues Startpasswort vergeben. Alle laufenden Anmeldungen des Benutzers enden, eine Sperre nach Fehlversuchen wird aufgehoben. Auch dieses Startpasswort muss der Benutzer bei der nächsten Anmeldung ändern.
- Deine eigene Rolle und deinen eigenen Zustand kann nur ein anderer Admin ändern. So bleibt immer mindestens ein aktiver Admin.
- Bei der Anmeldung spielt Groß-/Kleinschreibung des Benutzernamens keine Rolle.

## Mein Konto

Über deinen Benutzernamen oben rechts erreichst du „Mein Konto“. Dort änderst du dein eigenes Passwort:

- Das aktuelle Passwort ist nötig. Falsche Eingaben zählen wie Fehlversuche bei der Anmeldung: Nach 5 Fehlversuchen ist das Konto 15 Minuten gesperrt und überall abgemeldet.
- Das neue Passwort braucht mindestens 12 Zeichen und muss sich vom bisherigen unterscheiden.
- Nach dem Ändern bleibst du auf diesem Gerät angemeldet; auf allen anderen Geräten endet deine Anmeldung.

## Protokoll

Unter **Einstellungen → Protokoll** (nur für Admins) gibt es zwei Listen:

- **Änderungen:** wer wann was geändert hat, bei Einstellungen und Rollen mit altem und neuem Wert. Passwörter stehen nie darin, nur „Passwort geändert“ bzw. „zurückgesetzt“. Einträge bleiben unbegrenzt erhalten und können nicht geändert oder gelöscht werden.
- **Anmeldungen:** erfolgreiche und fehlgeschlagene Anmeldungen, Sperren und Abmeldungen. Bei unbekannten Benutzernamen wird der eingetippte Text nicht gespeichert. Einträge werden nach 365 Tagen automatisch gelöscht.

IP-Adressen werden nicht gespeichert. Die Zeiten erscheinen in der Zeitzone aus `MAKERHUB_TIMEZONE` (Standard: `Europe/Berlin`).

## Zwei-Faktor-Anmeldung

Mit Zwei-Faktor-Anmeldung braucht man zusätzlich zum Passwort einen 6-stelligen Code aus einer App auf dem Handy (z. B. Google Authenticator, Aegis oder 2FAS). Das funktioniert ohne Internet und ohne fremden Dienst – es wird keine SMS und keine E-Mail verschickt. Jeder Benutzer entscheidet selbst, ob er sie nutzt.

### Schlüssel einrichten (einmalig, Admin)

Die Zwei-Faktor-Geheimnisse werden verschlüsselt gespeichert. Den Schlüssel dafür erzeugst du einmal auf dem Server:

```
openssl rand -hex 32
```

Die Ausgabe (64 Zeichen) trägst du in die `.env` ein und startest die App neu:

```
MAKERHUB_2FA_KEY=<die Ausgabe von oben>
```

Bewahre eine Kopie des Schlüssels sicher auf (z. B. im Passwort-Manager), getrennt von der Datensicherung. Ohne Schlüssel bietet „Mein Konto“ das Einrichten nicht an.

### Einrichten (jeder Benutzer selbst)

1. **Mein Konto** → „Zwei-Faktor einrichten“, aktuelles Passwort eingeben.
2. Den QR-Code mit der App scannen (oder den angezeigten Schlüssel abtippen).
3. Den Code aus der App eingeben. Erst jetzt ist die Zwei-Faktor-Anmeldung eingeschaltet.
4. Die **10 Notfall-Codes** abschreiben oder ausdrucken. Sie werden nur dieses eine Mal angezeigt. Jeder funktioniert genau einmal, z. B. wenn das Handy nicht zur Hand ist. In „Mein Konto“ kannst du jederzeit neue erzeugen; die alten gelten dann nicht mehr.

Ausschalten geht ebenfalls in „Mein Konto“, mit Passwort und Code.

### Anmelden

Nach dem Passwort fragt MakerHub 3D nach dem Code. Dafür hast du 5 Minuten. Falsche Codes zählen wie falsche Passwörter: nach 5 Fehlversuchen ist das Konto 15 Minuten gesperrt. Jeder Code funktioniert nur einmal.

**Die Uhr des Servers muss stimmen.** Die Codes hängen von der Uhrzeit ab; eine Abweichung von etwa 30 Sekunden wird toleriert. Läuft die Server-Uhr falsch, werden alle Codes abgelehnt. Auf üblichen Linux-Servern stellt sich die Uhr automatisch über das Internet (NTP); prüfen kannst du sie mit `timedatectl`.

### Schlüssel verloren oder falsch eingetragen

Fehlt der Schlüssel oder passt er nicht zu den gespeicherten Einrichtungen, **startet die App nicht** und nennt im Container-Protokoll (`docker compose logs app`) zwei Auswege:

- den bisherigen Schlüssel wieder in die `.env` eintragen, oder
- wenn er verloren ist: die Zwei-Faktor-Anmeldung **aller** Benutzer löschen:

  ```
  docker compose run --rm app python -m app.cli reset-2fa
  ```

  Danach startet die App mit dem neuen Schlüssel. Jeder meldet sich wieder nur mit dem Passwort an und kann die Zwei-Faktor-Anmeldung neu einrichten. Sonst geht nichts verloren. Der Befehl steht im Änderungsprotokoll („Befehl auf dem Server“).
