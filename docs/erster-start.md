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
