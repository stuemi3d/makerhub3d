# MakerHub 3D – Projektplan

MakerHub 3D ist ein kleines, selbst gehostetes Tool für 3D-Druck-Kleingewerbe. Es schließt die Lücke zwischen [Bambuddy](https://github.com/maziggy/bambuddy) (Druckerverwaltung) und einer Rechnungs-Software (z. B. RechnungsFee oder Haben): **Artikel, Kalkulation, Produktion, Lager und Auswertung**. Es läuft als eigener Docker-Container und wird über einen „External Link“ in die Bambuddy-Seitenleiste eingebunden.

Status: **in Entwicklung, noch nicht für den produktiven Einsatz.**

## 1. Ziel und Umfang

Das Tool beantwortet für jeden Artikel eine Frage: *Was habe ich kalkuliert, was hat die Fertigung wirklich gekostet, und was habe ich beim Verkauf erzielt?*

**Drin (Version 1):**

- Artikelstamm mit Vorlagen und Varianten, jede Variante mit eigener Artikelnummer (z. B. `3D-0042-RT`); Kalkulation ist immer optional
- Kalkulation für noch nicht gedruckte Teile: Filament aus den Bambuddy-Spulen, Druckzeit und Gramm aus gesliceter 3MF oder G-Code, Strom, Maschine, Arbeitszeit, Zusatzteile, Ausschuss, Aufschlag
- Produktion: einen oder mehrere Drucke aus dem Bambuddy-Archiv einem oder mehreren Artikeln zuordnen; nicht zugeordnete (z. B. private) Drucke bleiben unberührt
- Lager: Zugänge aus Produktionen, Abgänge vorerst von Hand
- Auswertung pro Artikel: kalkuliert ↔ produziert ↔ verkauft, als Verlauf; Dashboard als Startseite

**Später:**

- Anbindung einer Rechnungs-Software (RechnungsFee: Zugänge übertragen, Verkäufe lesen; oder Haben: Verkäufe lesen)
- Preise pro Verkaufskanal (Plattformgebühren), FIFO-Bewertung

**Bewusst nicht drin:**

- Rechnungen, Angebote, Buchhaltung, EÜR → Rechnungs-Software
- Druckersteuerung, Spulen- und Filamentverwaltung → Bambuddy
- Kunden- und Auftragsverwaltung, Sets und Stücklisten

## 2. Architektur

- MakerHub 3D **liest** aus Bambuddy (REST-API mit eigenem, nur lesendem API-Key) und **schreibt nie** hinein.
- Die Rechnungs-Software wird später über einen **austauschbaren Anbindungs-Baustein** angebunden.
- Eigene SQLite-Datenbank für Artikel, Kalkulationen, Produktionen, Lager und Verkäufe.
- Einbindung in Bambuddy über „Einstellungen → Allgemein → Seitenleisten-Links → Externe Links“ (iframe). Das Einbetten wird nur für konfigurierte Ursprünge erlaubt.
- Erreichbar über einen Reverse Proxy (z. B. Caddy); der Container lauscht nur auf `127.0.0.1`.

## 3. Technik

**Python mit FastAPI, Oberfläche mit Jinja2-Templates und HTMX, Datenbank SQLite.** Das Tool besteht vor allem aus Formularen, Listen und einigen Diagrammen; ein React-Frontend wäre unnötiger Ballast. Falls die Oberfläche später zu eng wird, lässt sich ein anderes Frontend auf dieselbe API setzen.

| Kriterium | FastAPI + HTMX | FastAPI + React |
| --- | --- | --- |
| Sprachen | Python + HTML | Python + TypeScript |
| Code-Menge | etwa halb so viel | Frontend und Backend getrennt |
| Tests | pytest für alles | pytest + Vitest + Browser-Tests |
| Build | kein Frontend-Build | Node-Build im Image |

Weitere Bausteine:

- **Python 3.12**, Abhängigkeiten und Umgebung mit **uv** (Lockfile mit festen Versionen)
- **SQLAlchemy 2 + Alembic** für Datenbank und Migrationen
- **Pico CSS** mit dunklem Theme (passt optisch in den Bambuddy-Rahmen), mobil nutzbar
- **Chart.js** für Diagramme
- **httpx** als Client für Bambuddy und später die Rechnungs-Software
- **pytest** für alle Tests, **ruff** für Codestil und Formatierung
- Oberflächentexte übersetzbar (Deutsch Standard, Englisch nachrüstbar)

## 4. Datenmodell

Der Artikel ist der Knotenpunkt; alles andere hängt über die Artikelnummer an ihm. Durchgehende Regeln:

- Kosten und Preise werden **beim Buchen als Wert gespeichert** (spätere Preisänderungen verändern die Vergangenheit nicht).
- Der **Bestand wird nie gespeichert**, sondern aus den Lagerbewegungen summiert.
- Produktionen und Lagerbewegungen werden **nie gelöscht, sondern storniert** (Gegenbuchung).

| Tabelle | Wichtigste Felder | Verknüpfung nach außen |
| --- | --- | --- |
| Artikelvorlage | Name, Beschreibung, Merkmale (z. B. Farbe, Größe), Standardwerte für Preis und Gewichtung | nur Vorlage: kein Bestand, kein Verkauf |
| Artikel | Artikelnummer, Bezeichnung, optional Vorlage + Merkmalswerte (= Variante), Soll-Verkaufspreis, Gewichtung, Mindestbestand, aktiv | Artikelnummer = Schlüssel zur Rechnungs-Software |
| Kalkulation (optional) | Artikel oder Vorlage, Datum, „maßgeblich“-Kennzeichen, Materialzeilen (Spule/Durchschnitt/frei, Gramm, Preis/kg), Druckzeit, Teile pro Platte, Drucker, Arbeitszeit, Zusatzteile, Ausschuss %, Aufschlag %, Ergebnis pro Stück | Spule aus Bambuddy (nur gelesen) |
| Produktion | Datum, Notiz, Gesamtkosten, storniert | – |
| Produktion – Drucke | Produktion, Material-, Strom-, Verschleißkosten als Wert, Status (erfolgreich/fehlgeschlagen) | Bambuddy-Archiv-ID |
| Produktion – Ergebnis | Produktion, Artikel, gute Stück, Gewichtung, zugeteilte Kosten, Kennzeichen „aufgeteilt“ | – |
| Lagerbewegung | Artikel, Datum, Menge (+/−), Art (Produktion, Verkauf, Korrektur, Storno), Bezug | Produktion oder Verkauf |
| Verkauf | Artikel, Datum, Menge, Preis pro Stück, Quelle (Hand, RechnungsFee, Haben) | Rechnungsnummer bzw. externe ID |
| Ignoriert | Bambuddy-Archiv-ID, Datum | nicht relevante Drucke ausblenden |
| Gerät | Typ (Drucker/Trockner), Name, Anschaffungsdatum und -wert, Nutzungsjahre, Wartungsaufschlag, Druckstunden/Jahr bzw. Leistung (W) und Trocknungsstunden/Jahr | Drucker aus Bambuddy übernehmbar |
| Einstellungen | Strompreis (eigen oder aus Bambuddy), Arbeits-Stundensatz, Standard-Ausschuss, Standard-Aufschlag, Rundung, Spulen-Kategorie für „geschäftlich“, Abweichungsschwelle | – |
| Benutzer, Änderungsprotokoll | siehe Sicherheit | – |

**Vorlagen und Varianten:** Eine Vorlage beschreibt z. B. „Halter A“ mit dem Merkmal Farbe; jede Variante („Halter A rot“, `3D-0042-RT`) ist ein vollwertiger Artikel mit eigener Artikelnummer und eigenem Bestand. Artikel ohne Varianten brauchen keine Vorlage. Preis, Gewichtung und Kalkulation erbt die Variante von der Vorlage und kann sie überschreiben.

**Produktion:** fasst beliebig viele Drucke und beliebig viele Ergebnis-Artikel zusammen. Ein einziger Ergebnis-Artikel (z. B. Laterne aus 5 Platten) bekommt alle Kosten, ohne Schätzung. Bei mehreren Ergebnis-Artikeln: Aufteilung nach Stückzahl, optional mit Gewichtung pro Zeile (z. B. Halter zählt 3, Clip zählt 1); Vorschau vor dem Speichern, korrigierbar; in der Auswertung als „aufgeteilt“ markiert.

**Ausschuss und Fehldrucke:** Ausschuss auf einer durchgelaufenen Platte wird über weniger gute Stück eingepreist. Fehlgeschlagene Drucke werden rot angezeigt und können der Produktion zugeordnet werden, für die sie gedacht waren (erhöhen dann deren Stückkosten), oder ignoriert werden. Die Auswertung weist den Fehldruck-Anteil pro Artikel aus.

**Mehrere Kalkulationen:** pro Artikel bzw. Vorlage beliebig viele, mit Datum; neue als Kopie einer alten. Eine ist „maßgeblich“ und dient der Auswertung als Soll-Wert. Kalkulationen dürfen frei bearbeitet und gelöscht werden.

## 5. Seiten

| Seite | Inhalt |
| --- | --- |
| Dashboard (Startseite) | Kennzahlen des Zeitraums, Mindestbestand, nicht zugeordnete Drucke, Warnungen |
| Artikel | Vorlagen und Artikel anlegen; Variante aus Vorlage erzeugen. Artikel-Detail mit Kacheln **Kalkulationen · Produktionen · Lager · Verkäufe · Auswertung** (jeweils mit Anzahl); Klick öffnet die Tabelle mit Links zur Produktion und zum Bambuddy-Druck |
| Kalkulation | Teil kalkulieren: Spulen wählen, 3MF/G-Code hochladen oder Werte eintippen, Live-Berechnung, als Kalkulation am Artikel oder an der Vorlage speichern |
| Produktion | Liste erfolgreicher und fehlgeschlagener Bambuddy-Drucke; einen oder mehrere auswählen → Produktion mit Ergebnis-Artikeln und Stückzahl; Kostenaufteilung prüfen; oder „Ignorieren“ |
| Lager | Bestand je Artikel, Abgang/Korrektur von Hand, Bewegungsliste |
| Auswertung | pro Artikel, pro Vorlage und gesamt |
| Einstellungen | Werte, Geräte, Bambuddy-Verbindung (mit Verbindungstest), Benutzer (nur Admin) |

## 6. Kalkulation im Detail

Selbstkosten pro Stück = Summe der Bausteine ÷ Teile pro Platte. Standardwerte kommen aus den Einstellungen.

| Baustein | Berechnung | Werte |
| --- | --- | --- |
| Material | Gramm × Preis/kg, eine Zeile pro Filament/Farbe | Gramm aus 3MF/G-Code (inkl. Spülmenge); Preisquelle je Zeile: echte Bambuddy-Spule, Durchschnitt passender geschäftlicher Spulen oder frei (z. B. bestellte Spule) |
| Strom | Druckzeit × Ø Verbrauch pro Stunde × Strompreis | Ø Verbrauch aus echten Drucken im Bambuddy-Archiv |
| Maschine | Druckzeit × Druckerstundensatz | Stundensatz inkl. Trocknungsaufschlag |
| Arbeitszeit (optional) | Minuten × Stundensatz | leer = 0 |
| Zusatzteile | Menge × Preis | freie Liste; später Einkaufspreise aus der Rechnungs-Software |
| Ausschuss | Aufschlag in % | Standard aus Einstellungen |

Druckerstundensatz = Anschaffungswert ÷ (Nutzungsjahre × Druckstunden pro Jahr) × (1 + Wartungsaufschlag)

**Trocknung pauschal:** Die Trocknerkosten pro Jahr (Gerätekosten + Trocknungsstunden × Leistung × Strompreis) aller Trockner werden auf alle Druckstunden pro Jahr verteilt und auf den Druckerstundensatz aufgeschlagen.

**Verkaufspreis-Vorschlag:** Selbstkosten + Aufschlag in %, gerundet (z. B. auf ,50 oder ,90); Kleinunternehmer-tauglich (ohne Umsatzsteuer). Angezeigt werden Selbstkosten **ohne und mit Arbeitszeit**, Gewinn pro Stück und effektiver Stundenlohn.

**Spulen:** Geschäftliche Spulen werden über das Bambuddy-Feld „Kategorie“ erkannt (Wort in den Einstellungen festlegbar); die Kalkulation zeigt standardmäßig nur diese, mit Schalter „alle Spulen“. Der verwendete Preis wird in der Kalkulation gespeichert.

**Werte aus Bambuddy:** Strompreis und Druckerliste haben einen Schalter „aus Bambuddy übernehmen“; gesetzt = abgeglichen und gesperrt, sonst eigener Wert.

## 7. Auswertung und Dashboard

- **Pro Artikel:** kalkulierte vs. tatsächliche Kosten pro Stück, Ø Verkaufspreis, Gewinn pro Stück (€ und %), produziert / verkauft / Bestand, Fehldruck-Anteil; Diagramm „Kosten pro Stück über die Zeit“; Warnung bei Abweichung über X % (Standard 15 %).
- **Pro Vorlage:** alle Varianten nebeneinander.
- **Gesamt (Zeitraum wählbar):** Umsatz, Kosten der verkauften Teile, Rohgewinn, Top/Flop, Lagerwert, Ladenhüter, Fehldruckkosten, CSV-Export.
- **Kosten verkaufter Teile:** gleitender Durchschnitt; FIFO später umschaltbar.
- **Dashboard:** Verkauf (Umsatz, Rohgewinn, Ø Marge), Produktion (Stück, Kosten, Druckstunden, kg Filament, kWh), Qualität (Fehldruckquote/-kosten), Lager (Lagerwert, unter Mindestbestand), Zu erledigen (nicht zugeordnete Drucke, Warnungen, Ladenhüter); Diagramm „Umsatz und Kosten pro Monat“.
- **Verkäufe aus der Rechnungs-Software (M5):** Übernahme beim Festschreiben einer Rechnung, Zuordnung über die Artikelnummer, Liste „nicht zuordenbar“, Stornos buchen zurück, jede Rechnung nur einmal; Umsatz wahlweise nach Rechnungs- oder Zahlungsdatum. Bis dahin Verkäufe von Hand.

## 8. Sicherheit (Anwendung)

Das Tool wird von Anfang an so gebaut, dass es als Open Source veröffentlicht **und** aus dem Internet erreichbar sein kann.

**Zugang:**

- Eigener Login; die Einbindung in Bambuddy schützt nichts.
- Kein Standardpasswort: Der erste Admin wird beim ersten Start angelegt.
- Passwörter nur als Argon2-Hash; Sperre nach mehreren Fehlversuchen; sichere Sitzungs-Cookies; CSRF-Schutz.
- Zwei-Faktor (TOTP) in Version 1, abschaltbar.
- Änderungsprotokoll (wer hat wann was geändert) und Protokoll der Logins.

**Rollen (fest in Version 1):** Eine Installation = ein Betrieb.

| Recht | Admin | Mitarbeiter | Nur lesen |
| --- | --- | --- | --- |
| Ansehen (Dashboard, Artikel, Auswertung) | ja | ja | ja |
| Bearbeiten (Artikel, Kalkulation, Produktion, Lager, Verkäufe), Stornieren | ja | ja | nein |
| Einstellungen, Geräte, Bambuddy-Verbindung | ja | nein | nein |
| Benutzer, Sicherung, Änderungsprotokoll | ja | nein | nein |

**Geheimnisse:** Zugangsdaten (Bambuddy-Key, Sitzungsschlüssel, später Rechnungs-Software) nur über Umgebungsvariablen bzw. eine `.env`-Datei außerhalb des Repos; im Repo nur `.env.example` mit Platzhaltern. Zwei-Faktor-Geheimnisse verschlüsselt in der Datenbank. Keys in der Oberfläche nur maskiert.

**Bambuddy:** eigener API-Key mit ausschließlich Leserechten.

**Container:** läuft als normaler Benutzer, kein Docker-Socket, `cap_drop: ALL`, `no-new-privileges`, schreibgeschütztes Dateisystem außer `/data` und temporärem Ordner, Port nur auf `127.0.0.1`. Uploads mit Größenlimit, nur Metadaten auslesen, Schutz gegen manipulierte ZIP-Dateien (3MF), Datei danach löschen.

**Datensicherung:** nächtliche Sicherung der Datenbank nach `/data/backups` über die SQLite-Backup-Funktion; 14 Tages- und 12 Monatssicherungen; automatische Sicherung vor jeder Migration; „Sicherung herunterladen“ für Admins; getestete Wiederherstellung mit Anleitung.

**Steuern:** Das Tool führt keine steuerrelevanten Aufzeichnungen (Rechnungen, Kasse, Buchhaltung liegen in der Rechnungs-Software). Es ist trotzdem „GoBD-nah“ gebaut: Stornieren statt Löschen, Änderungsprotokoll.

## 9. Entwicklungsprozess

- Jede Aufgabe ist ein GitHub-Issue und wird als **ein** Pull Request umgesetzt.
- Vor dem Programmieren werden die **Testfälle in Klartext** im Issue festgehalten und vom Maintainer bestätigt.
- `main` ist geschützt: nur per Pull Request, Freigabe durch den Maintainer, Squash-Merge, keine Force-Pushes.
- Automatische Prüfungen bei jedem Pull Request: Tests, ruff, gitleaks (Geheimnisse), CodeQL, Dependency Review; Trivy für das Docker-Image.
- Vor jedem Pull Request prüft ein unabhängiger Review-Durchlauf (frischer Kontext) den Code gegen `CLAUDE.md` und die Sicherheits-Checkliste.
- Dependabot schlägt Updates als Pull Request vor.
- Das Docker-Image wird von GitHub Actions gebaut; das Aktualisieren der laufenden Instanz erfolgt immer von Hand.

**Testarten:**

| Bereich | Testart |
| --- | --- |
| Kostenrechnung | Unit-Tests mit festen Zahlen, auch Randfälle (0 Teile, fehlender Preis) |
| 3MF/G-Code | Tests mit echten, anonymisierten Beispieldateien |
| Bambuddy-Anbindung | aufgezeichnete Antworten; in CI läuft kein Bambuddy |
| Seiten und Formulare | FastAPI-Testclient |
| Migrationen | auf leerer und gefüllter Test-Datenbank |
| Sicherung | Sicherung erstellen und wiederherstellen |

## 10. Meilensteine

Jeder Meilenstein ist für sich nutzbar; danach wird das Tool eine Weile im Alltag eingesetzt, bevor der nächste startet.

**M0 – Grundgerüst**

- Projektgrundlagen: `CLAUDE.md`, Plan, Pull-Request-Vorlage, Issues
- Projektstruktur mit uv, FastAPI-App mit erster Seite, pytest und ruff
- GitHub Actions: Tests, ruff, gitleaks, CodeQL, Dependency Review
- Dockerfile und `compose.yml` (abgesichert wie oben), Image-Build in GitHub Actions, Trivy
- Grundlayout mit Navigation, dunklem Theme, Übersetzbarkeit; Einbetten nur für konfigurierte Ursprünge
- Datenbank mit Alembic-Migrationen, Seite Einstellungen
- Login, Benutzer und Rollen, Zwei-Faktor, Änderungsprotokoll
- Datensicherung

**M1 – Artikel und Lager**

- Vorlagen, Varianten und Artikel; Artikel-Detail mit Kacheln
- Lagerbewegungen, Bestand, Abgang und Korrektur von Hand, Storno
- Dashboard (erste Fassung) mit Mindestbestand

**M2 – Bambuddy-Anbindung und Produktion**

- Bambuddy-Client: Archiv-Liste, Archiv-Detail, Spulen, Drucker, Strompreis
- Seite Produktion: Drucke auswählen, Ergebnis-Artikel, Kostenaufteilung mit Vorschau, Fehldrucke, Ignorieren
- Artikel-Detail: Produktionen und Kosten pro Stück, Links zu Bambuddy

**M3 – Kalkulation**

- Geräte und Stundensätze inkl. Trocknungspauschale
- Rechenkern der Kalkulation
- 3MF- und G-Code-Auslesen
- Seite Kalkulation

**M4 – Verkauf und Auswertung**

- Verkauf von Hand
- Auswertung pro Artikel, pro Vorlage, gesamt; Dashboard vollständig
- CSV-Export

**M5 – Anbindung Rechnungs-Software (später)**

- Austauschbarer Anbindungs-Baustein
- RechnungsFee und/oder Haben

## 11. Offene Punkte

- Reicht beim Bambuddy-Key das Recht „Status lesen“ für Archiv und Spulen? (Test in M2)
- Liefern die APIs der Rechnungs-Software den Bezahlt-Status? (Prüfung zu Beginn von M5)
- Risiko: Bambuddy entwickelt sich sehr schnell → Anbindung in einem einzigen Baustein, Tests mit aufgezeichneten Antworten, Verbindungstest in den Einstellungen.
