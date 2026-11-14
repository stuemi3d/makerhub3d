# MakerHub 3D – Projektplan

MakerHub 3D ist ein kleines, selbst gehostetes Tool für 3D-Druck-Kleingewerbe. Es schließt die Lücke zwischen [Bambuddy](https://github.com/maziggy/bambuddy) (Druckerverwaltung) und einer Rechnungs-Software (z. B. RechnungsFee oder Haben): **Artikel, Kalkulation, Produktion, Lager und Auswertung**. Es läuft als eigener Docker-Container und wird über einen „External Link“ in die Bambuddy-Seitenleiste eingebunden.

Status: **in Entwicklung, noch nicht für den produktiven Einsatz.**

## 1. Ziel und Umfang

Das Tool beantwortet für jeden Artikel eine Frage: *Was habe ich kalkuliert, was hat die Fertigung wirklich gekostet, und was habe ich beim Verkauf erzielt?*

**Drin (Version 1):**

- Artikelstamm mit Vorlagen und Varianten, jede Variante mit eigener Artikelnummer (z. B. `123456-rt`, automatisch aus Vorlagennummer und Kürzeln der Merkmalswerte zusammengesetzt); Kalkulation ist immer optional
- Kalkulation für noch nicht gedruckte Teile: Filament aus den Bambuddy-Spulen, Druckzeit und Gramm aus gesliceter 3MF oder G-Code, Strom, Maschine, Ausschussquote, Arbeitszeit, Zusatzteile, Gewinnaufschlag
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
- Erreichbar nur über einen Reverse Proxy (z. B. Caddy): Die App lauscht im Container auf `0.0.0.0`; auf dem Server wird ihr Port nur auf `127.0.0.1` veröffentlicht, also nicht direkt von außen erreichbar.

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
- Produktionen und Lagerbewegungen werden **nie gelöscht, sondern storniert** (Gegenbuchung). Einzige Ausnahme: das endgültige Löschen eines Artikels durch einen Admin (siehe „Löschen von Artikeln und Vorlagen“).

| Tabelle | Wichtigste Felder | Verknüpfung nach außen |
| --- | --- | --- |
| Artikelvorlage | Vorlagennummer, Name, Beschreibung, Merkmale (z. B. Farbe, Größe; jeder Merkmalswert mit Kürzel), Standardwerte für Preis, Gewichtung und Mindestbestand, aktiv | nur Vorlage: kein Bestand, kein Verkauf |
| Artikel | Artikelnummer, Bezeichnung, optional Vorlage + Merkmalswerte (= Variante), Soll-Verkaufspreis, Gewichtung, Mindestbestand, aktiv | Artikelnummer = Schlüssel zur Rechnungs-Software |
| Kalkulation (optional) | Artikel oder Vorlage, Datum, „maßgeblich“-Kennzeichen, Materialzeilen (Spule/Durchschnitt/frei, Gramm, Preis/kg), Druckzeit, Teile pro Platte, Drucker, Ausschussquote %, Rüstzeit pro Platte (Min.), Nacharbeit pro Stück (Min.), Zusatzteile pro Stück, Gewinnaufschlag %, Ergebnis pro Stück (Selbstkosten ohne/mit Arbeitszeit, Verkaufspreis-Vorschlag) | Spule aus Bambuddy (nur gelesen) |
| Produktion | Datum, Notiz, Gesamtkosten, storniert | – |
| Produktion – Drucke | Produktion, Material-, Strom-, Verschleißkosten als Wert, Status (erfolgreich/fehlgeschlagen) | Bambuddy-Archiv-ID |
| Produktion – Ergebnis | Produktion, Artikel, gute Stück, Gewichtung, zugeteilte Kosten, Kennzeichen „aufgeteilt“ | – |
| Lagerbewegung | Artikel, Datum, Menge (+/−), Art (Produktion, Verkauf, Korrektur, Storno), Bezug | Produktion oder Verkauf |
| Verkauf | Artikel, Datum, Menge, Preis pro Stück, Quelle (Hand, RechnungsFee, Haben) | Rechnungsnummer bzw. externe ID |
| Ignoriert | Bambuddy-Archiv-ID, Datum | nicht relevante Drucke ausblenden |
| Gerät | Typ (Drucker/Trockner), Name, Anschaffungsdatum und -wert, Nutzungsjahre, Wartungsaufschlag, Druckstunden/Jahr bzw. Leistung (W) und Trocknungsstunden/Jahr | Drucker aus Bambuddy übernehmbar |
| Einstellungen | Strompreis (eigen oder aus Bambuddy), Arbeits-Stundensatz, Standard-Ausschussquote, Standard-Gewinnaufschlag, Rundung, Spulen-Kategorie für „geschäftlich“, Abweichungsschwelle | – |
| Benutzer, Änderungsprotokoll | siehe Sicherheit | – |

**Vorlagen und Varianten:** Eine Vorlage beschreibt z. B. „Halter A“ mit dem Merkmal Farbe; jede Variante („Halter A rot“, `123456-rt`) ist ein vollwertiger Artikel mit eigener Artikelnummer und eigenem Bestand. Artikel ohne Varianten brauchen keine Vorlage. Preis, Gewichtung, Mindestbestand und Kalkulation erbt die Variante von der Vorlage und kann sie überschreiben.

**Nummernschema:** Die Vorlage hat eine Vorlagennummer (z. B. `123456`), jeder Merkmalswert ein Kürzel (z. B. blau = `bl`, rot = `rt`, mittel = `m`). Beim Anlegen einer Variante wird die Artikelnummer automatisch zusammengesetzt: Vorlagennummer + Kürzel der gewählten Werte, in der Reihenfolge der Merkmale der Vorlage, getrennt durch `-` (z. B. `123456-bl-m`). Im Formular ist der Vorschlag vor dem Speichern änderbar; die Eindeutigkeit der Artikelnummer wird weiterhin geprüft. Artikel ohne Vorlage bekommen ihre Nummer wie bisher von Hand.

**Löschen von Artikeln und Vorlagen:** Normalfall ist „Deaktivieren“ (der Artikel verschwindet aus den Listen und kann wieder aktiviert werden). Löschen gibt es in vier Stufen:

1. **Nichts verknüpft:** „Löschen“ mit Rückfrage (Admin und Mitarbeiter; „Nur lesen“ nie).
2. **Nur Kalkulationen verknüpft:** Löschen möglich (Admin und Mitarbeiter); die Rückfrage nennt, was mitgelöscht wird, und rät zum Deaktivieren.
3. **Produktionen, Lagerbewegungen oder Verkäufe verknüpft:** für Mitarbeiter nur Deaktivieren, mit erklärendem Hinweis.
4. **Admin, „Endgültig löschen“:** löscht den Artikel samt allem Zugehörigen. Die Warnung nennt die Anzahl der betroffenen Datensätze und weist darauf hin, dass es nicht rückgängig zu machen ist; zur Bestätigung wird die Artikelnummer abgetippt. Das Änderungsprotokoll hält fest, wer wann welchen Artikel mit wie vielen Datensätzen gelöscht hat. Der Button wird nur angeboten, wenn Stufe 3 gilt (etwas hängt am Artikel). Gehört der Artikel zu einer Produktion mit mehreren Ergebnis-Artikeln, entfallen nur seine Ergebniszeile und seine Lagerbewegungen; Produktion und Kosten der übrigen Artikel bleiben unverändert (Kosten sind als Wert gespeichert). Dies ist die einzige Ausnahme von der Regel „nie löschen, sondern stornieren“; gedacht vor allem für Testdaten zu Beginn.

**Vorlagen** haben nie Bestand, Produktion oder Verkauf; für sie gelten deshalb nur die Stufen 1 und 2 plus Deaktivieren. Eine Vorlage lässt sich erst löschen, wenn alle ihre Varianten gelöscht sind; ihre Kalkulationen werden dann mitgelöscht (Rückfrage nennt die Anzahl). Eine deaktivierte Vorlage kann nicht mehr für neue Varianten genutzt werden; vorhandene Varianten bleiben unberührt.

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

**Oberfläche und Bedienung** (in zwei Stufen):

- **Stufe 1 (laufend, Bedienung und Orientierung):** Reiter-Leiste für zusammengehörige Bereiche (Artikel · Vorlagen · Merkmale), Pfad („Artikel › A-1001“) statt „Zurück“-Links, Detailseiten als Karte, kompakte Buttons, hilfreiche Hinweise bei leeren Listen. Links zu anderen Seiten bleiben zunächst normale Textlinks.
- **Stufe 2 (jetzt, in sechs Teilen, siehe M1b):** Gemeinsamer Stil nach dem Vorbild von Bambuddy (nachempfunden, nicht kopiert): Standardthemen **Dunkel** (Standard) und **Hell** mit Umschalter im Reiter „Darstellung“ der Einstellungen (für alle Rollen erreichbar, die Wahl wird im Browser gemerkt), **eine Navigationsleiste oben** statt einer eigenen Seitenleiste (MakerHub 3D wird schon aus der Seitenleiste von Bambuddy verlinkt), Kennzahlen-Kacheln, Tabellen mit sortierbaren Spalten, Chips und Aktionssymbolen, **Bearbeiten in Pop-ups** statt auf immer neuen Seiten (HTMX). Weitere Akzentfarben sind ausdrücklich später. Das Artikel-Detail bekommt außerdem ein Dashboard nach dem Vorbild von ERPNext: Von dort springt man in die jeweilige Liste (z. B. Preise, Lagerbuchungen).
- Optik-Feinschliff (Farben, Logo, Diagramm-Look) erst in Stufe 2.

## 6. Kalkulation im Detail

Gerechnet wird in dieser Reihenfolge. Standardwerte (Ausschussquote, Gewinnaufschlag, Arbeits-Stundensatz, Rundung) kommen aus den Einstellungen.

**Einheiten:** Eingaben in Gramm, Minuten, Watt und Prozent werden immer ausdrücklich umgerechnet: Prozentwert ÷ 100, Gramm ÷ 1000 → kg, Watt ÷ 1000 → kW, Minuten ÷ 60 → Stunden. Alle Zeiten (Druckzeit, Rüstzeit, Nacharbeit) gehen in Stunden in die Formeln ein.

1. **Druckkosten pro Platte** = Material + Strom + Maschine
2. **Druckkosten pro Stück** = Druckkosten pro Platte ÷ Teile pro Platte
3. **Druckkosten pro Stück inkl. Ausschuss** = Druckkosten pro Stück × (1 + Ausschussquote ÷ 100). Die Ausschussquote wirkt nur auf die Druckkosten.
4. **Arbeitszeit pro Stück** (optional, jedes leere Feld = 0):
   - in Stunden = (Rüstzeit pro Platte in Minuten ÷ Teile pro Platte + Nacharbeit pro Stück in Minuten) ÷ 60
   - als Kosten = Arbeitszeit pro Stück in Stunden × Arbeits-Stundensatz
5. **Zusatzteile pro Stück** = Summe aus Menge × Preis
6. **Selbstkosten pro Stück** = (3) + Arbeitskosten aus (4) + (5); angezeigt **ohne Arbeitszeit** ((3) + (5)) und **mit Arbeitszeit**
7. **Verkaufspreis-Vorschlag** = Selbstkosten pro Stück (mit Arbeitszeit) × (1 + Gewinnaufschlag ÷ 100), gerundet
8. **Verkaufspreis** = der am Artikel hinterlegte Soll-Verkaufspreis, falls gesetzt (bei Varianten ggf. von der Vorlage geerbt, siehe Abschnitt 4); sonst der Verkaufspreis-Vorschlag
9. **Gewinn pro Stück** = Verkaufspreis − Selbstkosten pro Stück (mit Arbeitszeit)
10. **Effektiver Stundenlohn** = (Verkaufspreis − Selbstkosten pro Stück ohne Arbeitszeit) ÷ Arbeitszeit pro Stück in Stunden. Ist die Arbeitszeit 0, wird „–“ angezeigt (keine Division).

| Baustein | Bezug | Berechnung | Werte |
| --- | --- | --- | --- |
| Material | pro Platte | Gramm ÷ 1000 × Preis/kg, eine Zeile pro Filament/Farbe | Gramm aus 3MF/G-Code (inkl. Spülmenge); Preisquelle je Zeile: echte Bambuddy-Spule, Durchschnitt passender geschäftlicher Spulen oder frei (z. B. bestellte Spule) |
| Strom | pro Platte | Druckzeit in Stunden × Ø Verbrauch in kWh pro Stunde × Strompreis pro kWh | Ø Verbrauch aus echten Drucken im Bambuddy-Archiv |
| Maschine | pro Platte | Druckzeit in Stunden × Druckerstundensatz | Stundensatz inkl. Trocknungsaufschlag |
| Ausschussquote | auf Druckkosten | × (1 + Ausschussquote ÷ 100) | Standard aus Einstellungen |
| Rüstzeit (optional) | pro Platte | Minuten ÷ Teile pro Platte ÷ 60 × Arbeits-Stundensatz | z. B. Platte vorbereiten, Druck starten, abnehmen; leer = 0 |
| Nacharbeit (optional) | pro Stück | Minuten ÷ 60 × Arbeits-Stundensatz | z. B. Stützen entfernen, entgraten, verpacken; leer = 0 |
| Zusatzteile | pro Stück | Menge × Preis | freie Liste; später Einkaufspreise aus der Rechnungs-Software |
| Gewinnaufschlag | auf Selbstkosten | × (1 + Gewinnaufschlag ÷ 100) | Standard aus Einstellungen |

Druckerstundensatz = Anschaffungswert ÷ (Nutzungsjahre × Druckstunden pro Jahr) × (1 + Wartungsaufschlag in % ÷ 100)

**Trocknung pauschal:** Die Trocknerkosten pro Jahr (Gerätekosten + Trocknungsstunden × Leistung in Watt ÷ 1000 × Strompreis pro kWh) aller Trockner werden auf alle Druckstunden pro Jahr verteilt und auf den Druckerstundensatz aufgeschlagen.

**Verkaufspreis-Vorschlag:** wie in Schritt 7, gerundet (z. B. auf ,50 oder ,90); Kleinunternehmer-tauglich (ohne Umsatzsteuer). Angezeigt werden Selbstkosten **ohne und mit Arbeitszeit**, Verkaufspreis-Vorschlag, Gewinn pro Stück und effektiver Stundenlohn (Schritte 8–10).

**Spulen:** Geschäftliche Spulen werden über das Bambuddy-Feld „Kategorie“ erkannt (Wort in den Einstellungen festlegbar); die Kalkulation zeigt standardmäßig nur diese, mit Schalter „alle Spulen“. Der verwendete Preis wird in der Kalkulation gespeichert.

**Werte aus Bambuddy:** Strompreis und Druckerliste haben einen Schalter „aus Bambuddy übernehmen“; gesetzt = abgeglichen und gesperrt, sonst eigener Wert.

## 7. Auswertung und Dashboard

- **Pro Artikel:** kalkulierte vs. tatsächliche Kosten pro Stück, Ø Verkaufspreis, Gewinn pro Stück (€ und %) und effektiver Stundenlohn – berechnet wie in Abschnitt 6 (Schritte 9 und 10), aber mit tatsächlichen Werten: echter Verkaufspreis und tatsächliche Produktionskosten statt Kalkulation; produziert / verkauft / Bestand, Fehldruck-Anteil; Diagramm „Kosten pro Stück über die Zeit“; Warnung bei Abweichung über X % (Standard 15 %).
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
| Artikel und Vorlagen löschen oder deaktivieren (Stufen 1–3) | ja | ja | nein |
| Artikel endgültig löschen (Stufe 4) | ja | nein | nein |
| Einstellungen, Geräte, Bambuddy-Verbindung | ja | nein | nein |
| Benutzer, Sicherung, Änderungsprotokoll | ja | nein | nein |

**Geheimnisse:** Zugangsdaten (Bambuddy-Key, Sitzungsschlüssel, später Rechnungs-Software) nur über Umgebungsvariablen bzw. in einer `.env`-Datei, die nie ins Repository kommt; im Repo nur `.env.example` mit Platzhaltern. Zwei-Faktor-Geheimnisse verschlüsselt in der Datenbank. Keys in der Oberfläche nur maskiert.

**Bambuddy:** eigener API-Key mit ausschließlich Leserechten.

**Container:** läuft als normaler Benutzer, kein Docker-Socket, `cap_drop: ALL`, `no-new-privileges`, schreibgeschütztes Dateisystem außer `/data` und temporärem Ordner; die App lauscht im Container auf `0.0.0.0`, der Port wird auf dem Server nur auf `127.0.0.1` veröffentlicht und ist nur über den Reverse Proxy erreichbar. Uploads mit Größenlimit, nur Metadaten auslesen, Schutz gegen manipulierte ZIP-Dateien (3MF), Datei danach löschen.

**Datensicherung:** nächtliche Sicherung der Datenbank nach `/data/backups` über die SQLite-Backup-Funktion; 14 Tages- und 12 Monatssicherungen; automatische Sicherung vor jeder Migration; „Sicherung herunterladen“ für Admins; getestete Wiederherstellung mit Anleitung.

**Steuern:** Das Tool führt keine steuerrelevanten Aufzeichnungen (Rechnungen, Kasse, Buchhaltung liegen in der Rechnungs-Software). Es ist trotzdem „GoBD-nah“ gebaut: Stornieren statt Löschen (einzige Ausnahme: endgültiges Löschen eines Artikels durch einen Admin, protokolliert), Änderungsprotokoll.

## 9. Entwicklungsprozess

- Jede Aufgabe ist ein GitHub-Issue und wird als **ein** Pull Request umgesetzt.
- Vor dem Programmieren werden die **Testfälle in Klartext** im Issue festgehalten und vom Maintainer bestätigt.
- `main` ist geschützt: nur per Pull Request, Freigabe durch den Maintainer, Squash-Merge, keine Force-Pushes.
- Automatische Prüfungen bei jedem Pull Request: Tests, ruff, gitleaks (Geheimnisse), CodeQL, Dependency Review; Trivy für das Docker-Image.
- Vor jedem Pull Request prüft ein unabhängiger Review-Durchlauf (frischer Kontext) die Änderungen gegen `CLAUDE.md` und die Sicherheits-Checkliste.
- Dependabot schlägt Updates als Pull Request vor.
- Das Docker-Image wird von GitHub Actions gebaut; das Aktualisieren der laufenden Instanz erfolgt immer von Hand.

**Testarten:**

| Bereich | Testart |
| --- | --- |
| Kostenrechnung | Unit-Tests mit festen Zahlen, auch Randfälle (0 Teile, fehlender Preis) |
| 3MF/G-Code | Tests mit Beispieldateien, die der Maintainer bereitstellt (selbst gesliced, z. B. ein Würfel); vor dem Einchecken werden Benutzernamen, Seriennummern, Pfade und ähnliche Metadaten entfernt. Alle übrigen Beispieldaten sind erfunden. |
| Bambuddy-Anbindung | echt aufgezeichnete, dann bereinigte Antworten: Druckernamen, Seriennummern, Dateinamen, Benutzernamen, IP-Adressen/Hostnamen und andere persönliche Werte werden durch erfundene ersetzt; in CI läuft kein Bambuddy |
| Seiten und Formulare | FastAPI-Testclient |
| Migrationen | auf leerer und gefüllter Test-Datenbank |
| Sicherung | Sicherung erstellen und wiederherstellen |

## 10. Meilensteine

Jeder Meilenstein ist für sich nutzbar; danach wird das Tool eine Weile im Alltag eingesetzt, bevor der nächste startet.

Erledigte Punkte sind mit `[x]` abgehakt. Das Abhaken gehört in den Pull Request, der den Punkt erledigt.

**M0 – Grundgerüst**

- [x] Projektgrundlagen: `CLAUDE.md`, Plan, Pull-Request-Vorlage, Issues
- [x] Projektstruktur mit uv, FastAPI-App mit erster Seite, pytest und ruff
- [x] GitHub Actions: Tests, ruff, gitleaks, CodeQL, Dependency Review
- [x] Dockerfile und `compose.yml` (abgesichert wie oben), Image-Build in GitHub Actions, Trivy
- [x] Grundlayout mit Navigation, dunklem Theme, Übersetzbarkeit; Einbetten nur für konfigurierte Ursprünge
- [x] Datenbank mit Alembic-Migrationen, Seite Einstellungen
- [x] Login, Benutzer und Rollen, Zwei-Faktor, Änderungsprotokoll
- [x] Datensicherung

**M1 – Artikel und Lager**

- [x] Vorlagen, Varianten und Artikel; Artikel-Detail mit Kacheln
- [x] Lagerbewegungen, Bestand, Abgang und Korrektur von Hand, Storno
- [x] Dashboard (erste Fassung) mit Mindestbestand

**M1b – Oberfläche Stufe 2**

Der Maintainer hat die frühere Bedingung („erst nach Alltagsnutzung von M1“) aufgehoben: Ein gemeinsamer Stil vor den weiteren Seiten spart spätere Umbauten. Übersicht und Reihenfolge im Issue #55; eine Aufgabe nach der anderen:

- [x] 1/6 Design-Grundlage: Stil-Variablen, Dunkel (Standard) und Hell, Umschalter im Einstellungs-Reiter „Darstellung“ (für alle Rollen), Karten, Buttons, Chips (#73)
- [x] 2/6 Gesamtlayout mit Navigationsleiste oben, Handy-Menü (#74)
- [x] 3/6 Tabellen-Baustein (sortierbar, Chips, Aktionssymbole) und Artikelübersicht (#75)
- [x] 4/6 Bearbeiten in Pop-ups (HTMX), zuerst Artikel anlegen und bearbeiten (#76)
- [x] 5a/6 Artikel-Detail und Startseite im neuen Stil, „Bearbeiten“ im Pop-up (#77)
- [ ] 5b/6 Varianten und Lager als Pop-ups, Lager-Listen mit dem Tabellen-Baustein (#87)
- [ ] 6/6 Übrige Seiten im neuen Stil, Vorlagen-Detail als Karte (#78)
- [ ] Artikel-Dashboard nach dem Vorbild von ERPNext: Von der Übersicht springt man in die jeweilige Liste (Preise, Lagerbuchungen …)
- [ ] Feinschliff: Logo, Diagramm-Optik, echte Ein/Aus-Schalter, weitere Akzentfarben (optional)

**M2 – Bambuddy-Anbindung und Produktion**

- [ ] Bambuddy-Client: Archiv-Liste, Archiv-Detail, Spulen, Drucker, Strompreis
- [ ] Seite Produktion: Drucke auswählen, Ergebnis-Artikel, Kostenaufteilung mit Vorschau, Fehldrucke, Ignorieren
- [ ] Artikel-Detail: Produktionen und Kosten pro Stück, Links zu Bambuddy

**M3 – Kalkulation**

- [ ] Geräte und Stundensätze inkl. Trocknungspauschale
- [ ] Rechenkern der Kalkulation
- [ ] 3MF- und G-Code-Auslesen
- [ ] Seite Kalkulation

**M4 – Verkauf und Auswertung**

- [ ] Verkauf von Hand
- [ ] Auswertung pro Artikel, pro Vorlage, gesamt; Dashboard vollständig
- [ ] CSV-Export

**M5 – Anbindung Rechnungs-Software (später)**

- [ ] Austauschbarer Anbindungs-Baustein
- [ ] RechnungsFee und/oder Haben

## 11. Offene Punkte

- Reicht beim Bambuddy-Key das Recht „Status lesen“ für Archiv und Spulen? (Test in M2)
- Liefern die APIs der Rechnungs-Software den Bezahlt-Status? (Prüfung zu Beginn von M5)
- Risiko: Bambuddy entwickelt sich sehr schnell → Anbindung in einem einzigen Baustein, Tests mit aufgezeichneten Antworten, Verbindungstest in den Einstellungen.
- **Umfang der tatsächlichen Kosten** (M2/M4): Eine Produktion erfasst bisher nur Material-, Strom- und Verschleißkosten, keine Arbeitszeit und keine Zusatzteile. Gewinn und effektiver Stundenlohn mit tatsächlichen Werten (Abschnitt 7) sind so nicht berechenbar, und der Vergleich kalkuliert ↔ tatsächlich vergleicht unterschiedliche Kostenumfänge. *Vorschlag:* Arbeitszeit und Zusatzteile werden beim Anlegen einer Produktion aus der maßgeblichen Kalkulation vorbelegt und können dort angepasst werden; die Abweichungswarnung vergleicht nur Druckkosten mit Druckkosten.
- **Verkaufspreis bei einer Kalkulation an einer Vorlage** (M3): Schritt 8 in Abschnitt 6 geht vom Soll-Verkaufspreis eines Artikels aus; eine Kalkulation an einer Vorlage hat aber keinen Artikel, und für diesen Fall ist kein Preis festgelegt. *Vorschlag:* Es gilt der Standardpreis der Vorlage; ein Wert am Artikel überschreibt ihn.
- **Druckerstundensatz mit Trocknung** (M3): Die Formel in Abschnitt 6 enthält den Trocknungsaufschlag noch nicht, und die Gerätekosten eines Trockners pro Jahr sind nicht festgelegt. *Vorschlag:* Druckerstundensatz = Anschaffungswert ÷ (Nutzungsjahre × Druckstunden pro Jahr) × (1 + Wartungsaufschlag ÷ 100) + Trocknungsaufschlag; Gerätekosten eines Trockners pro Jahr = Anschaffungswert ÷ Nutzungsjahre × (1 + Wartungsaufschlag ÷ 100).
