# MakerHub 3D – Projektplan

MakerHub 3D ist ein kleines, selbst gehostetes Tool für 3D-Druck-Kleingewerbe. Es schließt die Lücke zwischen [Bambuddy](https://github.com/maziggy/bambuddy) (Druckerverwaltung) und einer Rechnungs-Software (z. B. RechnungsFee oder Haben): **Artikel, Kalkulation, Produktion, Lager und Auswertung**. Es läuft als eigener Docker-Container und wird über einen „External Link“ in die Bambuddy-Seitenleiste eingebunden.

Status: **in Entwicklung, noch nicht für den produktiven Einsatz.**

## 1. Ziel und Umfang

Das Tool beantwortet für jeden Artikel eine Frage: *Was habe ich kalkuliert, was hat die Fertigung wirklich gekostet, und was habe ich beim Verkauf erzielt?*

**Drin (Version 1):**

- Artikelstamm mit Vorlagen und Varianten, jede Variante mit eigener Artikelnummer (z. B. `123456-rt`, automatisch aus Vorlagennummer und Kürzeln der Merkmalswerte zusammengesetzt); Kalkulation ist immer optional
- Kalkulation für noch nicht gedruckte Teile: Filament aus den Bambuddy-Spulen, Druckzeit und Gramm aus einer gesliceten `.gcode.3mf`-Datei (oder von Hand eingetragen), Strom, Maschine, Ausschussquote, Arbeitszeit, Gewinnaufschlag
- Produktion: einen oder mehrere Drucke aus dem Bambuddy-Archiv genau einem Artikel zuordnen; nicht zugeordnete (z. B. private) Drucke bleiben unberührt
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
| Artikelvorlage | Vorlagennummer, Name, Beschreibung, Merkmale (z. B. Farbe, Größe; jeder Merkmalswert mit Kürzel), Standardwerte für Preis und Mindestbestand, aktiv | nur Vorlage: kein Bestand, kein Verkauf |
| Artikel | Artikelnummer, Bezeichnung, optional Vorlage + Merkmalswerte (= Variante), Soll-Verkaufspreis, Mindestbestand, aktiv | Artikelnummer = Schlüssel zur Rechnungs-Software |
| Kalkulation (optional) | Artikel (auch eine Variante, nie eine Vorlage), Datum, „maßgeblich“-Kennzeichen, Art (mit Datei / von Hand), optional die gespeicherte `.gcode.3mf`-Datei, Materialzeilen (Material, Gramm; je Zeile eine oder mehrere Bambuddy-Spulen mit Gramm und Preis/kg als Wert), Druckzeit, Teile pro Platte, Drucker, Ausschussquote %, Rüstzeit pro Platte (Min.), Nacharbeit pro Stück (Min.), Gewinnaufschlag %, Ergebnis pro Stück (Selbstkosten ohne/mit Arbeitszeit, Verkaufspreis-Vorschlag) | Spule aus Bambuddy (nur gelesen) |
| Produktion | Datum, Notiz, Gesamtkosten, Arbeitszeit (Minuten), Stundensatz und Arbeitskosten als Wert, Fehldruck-Stücke (nur zur Information, ändern weder Bestand noch Kosten), storniert | – |
| Produktion – Drucke | Produktion, Material-, Strom-, Maschinenkosten als Wert (Maschinenkosten = Druckstunden × Maschinensatz des gewählten Druckers, Satz siehe Abschnitt 6; der Verschleiß steckt im Satz und wird nicht aus Bambuddy übernommen), Druckzeit, Filament in Gramm und Strom in kWh als Wert (leer, wenn Bambuddy sie nicht kennt), Filament (Material als Text, Farben als Farbcodes), Status (erfolgreich/fehlgeschlagen) | Bambuddy-Archiv-ID |
| Produktion – Ergebnis | Produktion, Artikel (genau einer), gute Stück, Kosten (alle Kosten der Produktion) | – |
| Lagerbewegung | Artikel, Datum, Menge (+/−), Art (Produktion, Verkauf, Korrektur, Storno), Bezug | Produktion oder Verkauf |
| Verkauf | Artikel, Datum, Menge, Preis pro Stück, Quelle (Hand, RechnungsFee, Haben) | Rechnungsnummer bzw. externe ID |
| Ignoriert | Bambuddy-Archiv-ID, Datum | nicht relevante Drucke ausblenden |
| Druckermodell | Name (nur aus den Modellen wählbar, die Bambuddy meldet), Tabelle **Material → Stromverbrauch in kWh pro Stunde** (z. B. PLA 0,10; PETG 0,13). Mehrere Drucker desselben Modells nutzen dieselbe Tabelle. Die Tabelle hat beliebig viele Zeilen (technisch höchstens 100); die Reihenfolge der Zeilen bestimmt der Nutzer (Pfeile oder Ziehen) und sie gilt in der Übersicht (eine Zeile pro Material) und in der Auswahl der Kalkulation. Löschen nur, solange kein Drucker das Modell nutzt. Ohne Modell oder ohne Tabelle ist keine Kalkulation möglich (Hinweis: zuerst eine Tabelle anlegen). Passt ein Material aus der Datei nicht genau (z. B. „PLA-CF“ bei „PLA“), wählt der Nutzer ein passendes Material aus der Tabelle und bekommt den Hinweis, den Filamenttyp künftig dort zu hinterlegen; es wird nie geraten und nie mit 0 gerechnet. Ein Ersatzwert in den Einstellungen entfällt. Später denkbar: Knopf an der Tabelle „Durchschnitt aus Bambuddy holen“ | Modelle aus der Bambuddy-Druckerliste (nur lesend) |
| Gerät | Typ (Drucker/Trockner), Name, Anschaffungsdatum und -wert, Nutzungsjahre, Wartungsaufschlag (%), nur Drucker: Druckstunden/Jahr und Druckermodell (optional; wird bei einem Bambuddy-Drucker automatisch gewählt, wenn ein Modell mit gleichem Namen existiert); aktiv/deaktiviert; Bambuddy-Drucker-ID, wenn ein Drucker aus Bambuddy übernommen wurde (dann ist der Name gesperrt). Restwert und Stundensatz werden nur angezeigt, nicht gespeichert | Drucker aus Bambuddy übernehmbar (nur lesend); ein Gerät lässt sich löschen, solange keine Produktion daran hängt, sonst nur deaktivieren |
| Einstellungen | Strompreis (eigen oder aus Bambuddy), Arbeits-Stundensatz, Standard-Ausschussquote, Standard-Gewinnaufschlag, Rundung, Spulen-Kategorie für „geschäftlich“ und Schalter „nur geschäftliche Spulen anzeigen“ (Standard: aus), Abweichungsschwelle | – |
| Benutzer, Änderungsprotokoll | siehe Sicherheit | – |

**Vorlagen und Varianten:** Eine Vorlage beschreibt z. B. „Halter A“ mit dem Merkmal Farbe; jede Variante („Halter A rot“, `123456-rt`) ist ein vollwertiger Artikel mit eigener Artikelnummer und eigenem Bestand. Artikel ohne Varianten brauchen keine Vorlage. Preis und Mindestbestand erbt die Variante von der Vorlage und kann sie überschreiben. Kalkulationen gehören immer zu einem Artikel oder einer Variante, nie zur Vorlage: Größen und Varianten können anders kosten.

**Nummernschema:** Die Vorlage hat eine Vorlagennummer (z. B. `123456`), jeder Merkmalswert ein Kürzel (z. B. blau = `bl`, rot = `rt`, mittel = `m`). Beim Anlegen einer Variante wird die Artikelnummer automatisch zusammengesetzt: Vorlagennummer + Kürzel der gewählten Werte, in der Reihenfolge der Merkmale der Vorlage, getrennt durch `-` (z. B. `123456-bl-m`). Im Formular ist der Vorschlag vor dem Speichern änderbar; die Eindeutigkeit der Artikelnummer wird weiterhin geprüft. Artikel ohne Vorlage bekommen ihre Nummer wie bisher von Hand.

**Löschen von Artikeln und Vorlagen:** Normalfall ist „Deaktivieren“ (der Artikel verschwindet aus den Listen und kann wieder aktiviert werden). Löschen gibt es in vier Stufen:

1. **Nichts verknüpft:** „Löschen“ mit Rückfrage (Admin und Mitarbeiter; „Nur lesen“ nie).
2. **Nur Kalkulationen verknüpft:** Löschen möglich (Admin und Mitarbeiter); die Rückfrage nennt, was mitgelöscht wird, und rät zum Deaktivieren.
3. **Produktionen, Lagerbewegungen oder Verkäufe verknüpft:** für Mitarbeiter nur Deaktivieren, mit erklärendem Hinweis.
4. **Admin, „Endgültig löschen“:** löscht den Artikel samt allem Zugehörigen (bei Kalkulationen auch die gespeicherten Dateien). Die Warnung nennt die Anzahl der betroffenen Datensätze und weist darauf hin, dass es nicht rückgängig zu machen ist; zur Bestätigung wird die Artikelnummer abgetippt. Das Änderungsprotokoll hält fest, wer wann welchen Artikel mit wie vielen Datensätzen gelöscht hat. Der Button wird nur angeboten, wenn Stufe 3 gilt (etwas hängt am Artikel). Dies ist die einzige Ausnahme von der Regel „nie löschen, sondern stornieren“; gedacht vor allem für Testdaten zu Beginn.

**Vorlagen** haben nie Bestand, Produktion oder Verkauf; für sie gelten deshalb nur die Stufen 1 und 2 plus Deaktivieren. Eine Vorlage lässt sich erst löschen, wenn alle ihre Varianten gelöscht sind. Eine deaktivierte Vorlage kann nicht mehr für neue Varianten genutzt werden; vorhandene Varianten bleiben unberührt.

**Produktion:** fasst beliebig viele Drucke (Platten) und **genau einen Ergebnis-Artikel** zusammen. Dieser Artikel bekommt alle Kosten, ohne Schätzung und ohne Aufteilung. Erlaubt sind: ein Artikel auf einer Platte, derselbe Artikel mehrfach auf einer Platte, ein Artikel über mehrere Platten (z. B. Laterne aus 5 Platten) und derselbe Artikel mehrfach über mehrere Platten; die Kosten pro Stück sind alle Kosten geteilt durch die guten Stück. Vorschau vor dem Buchen. Wer mehrere Artikel druckt, legt je Artikel eine eigene Produktion mit eigenen Platten an. Gemischte Platten mit mehreren Artikeln sind bewusst nicht vorgesehen: Bambuddy liefert nur Kosten pro Druck, und eine Aufteilung nach Stückzahl oder von Hand eingetragener Gewichtung ist zu ungenau. Möglicher späterer Ausbau (nur bei Bedarf): Aufteilung nach dem Filamentgewicht pro Objekt, das sich aus den Objekt-Markierungen im G-Code der Platte berechnen lässt.

**Drucker einer Produktion (#162):** Jeder Druck einer Produktion hat einen Drucker (ein aktives Gerät vom Typ Drucker). Er wird vorgewählt, wenn der Bambuddy-Drucker des Archiv-Eintrags einem Gerät zugeordnet ist; sonst wählt der Nutzer ihn von Hand (Pflichtfeld, nur aktive Drucker). Die Maschinenkosten werden vorgeschlagen als Druckstunden × Maschinensatz (Stundensatz des Druckers + Trocknungsaufschlag, siehe Abschnitt 6), sind überschreibbar und werden als Wert gespeichert. Es gibt keinen Ersatzwert: Ist kein Drucker angelegt, steht ein Hinweis da und es lässt sich nicht buchen.

**Ausschuss und Fehldrucke:** Ausschuss auf einer durchgelaufenen Platte wird über weniger gute Stück eingepreist. Fehlgeschlagene Drucke werden rot angezeigt und können der Produktion zugeordnet werden, für die sie gedacht waren (erhöhen dann deren Stückkosten), oder ignoriert werden. Die Auswertung weist den Fehldruck-Anteil pro Artikel aus.

**Mehrere Kalkulationen:** pro Artikel (auch Variante) beliebig viele, mit Datum; neue als Kopie einer alten. Eine ist „maßgeblich“ und dient der Auswertung als Soll-Wert. Kalkulationen dürfen frei bearbeitet und gelöscht werden; beim Löschen wird auch die gespeicherte Datei gelöscht.

## 5. Seiten

| Seite | Inhalt |
| --- | --- |
| Dashboard (Startseite) | Kennzahlen des Zeitraums, Mindestbestand, nicht zugeordnete Drucke, Warnungen |
| Artikel | Vorlagen und Artikel anlegen; Variante aus Vorlage erzeugen. Artikel-Detail mit Kacheln **Kalkulationen · Produktionen · Lager · Verkäufe · Auswertung** (jeweils mit Anzahl); Klick öffnet die Tabelle mit Links zur Produktion und zum Bambuddy-Druck |
| Kalkulation | Eigene Liste aller Kalkulationen wie bei der Produktion (Artikel, Datum, Drucker, Selbstkosten, Verkaufspreis-Vorschlag, „maßgeblich“, Symbol-Buttons rechts); oben rechts „Neue Kalkulation“. Neu, Bearbeiten, Kopieren und Ansehen sind Pop-ups; im Formular: Artikel (oder Variante), „mit Datei“ oder „von Hand“, Drucker, Werte (aus der `.gcode.3mf` oder eingetippt), Spulen, Arbeit; Live-Berechnung; Speichern nur mit gewähltem Artikel oder gewählter Variante (ohne bleibt es eine Schnellkalkulation), die Datei wird mitgespeichert. Die Kachel „Kalkulationen“ am Artikel öffnet dieselbe Liste, gefiltert auf diesen Artikel |
| Produktion | Liste erfolgreicher und fehlgeschlagener Bambuddy-Drucke; einen oder mehrere auswählen → Produktion mit einem Ergebnis-Artikel und den guten Stück; Kosten pro Stück in der Vorschau prüfen; oder „Ignorieren“ |
| Lager | Bestand je Artikel, Abgang/Korrektur von Hand, Bewegungsliste |
| Auswertung | pro Artikel, pro Vorlage und gesamt |
| Einstellungen | Werte, Geräte, Bambuddy-Verbindung (mit Verbindungstest), Benutzer (nur Admin) |

**Oberfläche und Bedienung** (in zwei Stufen):

- **Stufe 1 (laufend, Bedienung und Orientierung):** Reiter-Leiste für zusammengehörige Bereiche (Artikel · Vorlagen · Merkmale), Pfad („Artikel › A-1001“) statt „Zurück“-Links, Detailseiten als Karte, kompakte Buttons, hilfreiche Hinweise bei leeren Listen. Links zu anderen Seiten bleiben zunächst normale Textlinks.
- **Stufe 2 (jetzt, in sechs Teilen, siehe M1b):** Gemeinsamer Stil nach dem Vorbild von Bambuddy (nachempfunden, nicht kopiert): Standardthemen **Dunkel** (Standard) und **Hell** mit Umschalter im Reiter „Darstellung“ der Einstellungen (für alle Rollen erreichbar, die Wahl wird im Browser gemerkt), **eine Navigationsleiste oben** statt einer eigenen Seitenleiste (MakerHub 3D wird schon aus der Seitenleiste von Bambuddy verlinkt), Kennzahlen-Kacheln, Tabellen mit sortierbaren Spalten, Chips und Aktionssymbolen, **Bearbeiten in Pop-ups** statt auf immer neuen Seiten (HTMX). Weitere Akzentfarben sind ausdrücklich später. Das Artikel-Detail bekommt außerdem ein Dashboard nach dem Vorbild von ERPNext: Von dort springt man in die jeweilige Liste (z. B. Preise, Lagerbuchungen).
- Optik-Feinschliff (Farben, Logo, Diagramm-Look) erst in Stufe 2.

## 6. Kalkulation im Detail

**Ablauf:** Der Menüpunkt „Kalkulation“ zeigt die Liste aller Kalkulationen (oder, über die Kachel am Artikel, die eines Artikels). Beim Klick auf „Neue Kalkulation“ öffnet sich das Formular als Pop-up; den Artikel (oder die Variante) wählt man im Formular, bei der Kachel eines Artikels ist er vorgewählt:

1. Man wählt „mit Datei“ oder „von Hand“.
2. Man wählt den Drucker (aktive Drucker; sein Modell braucht eine Stromtabelle).
3. Werte: Mit Datei lädt man eine `.gcode.3mf` hoch (nur dieses Format; Größenlimit und Typprüfung); die Seite zeigt übersichtlich Druckzeit sowie Gramm und Zeitanteil je Material und Farbe. Von Hand trägt man diese Werte selbst ein. Der Zeitanteil in Minuten erscheint nur, wenn es mehr als ein Material gibt (bei einem Material ist er die Druckzeit); beim zweiten Material ist der erste mit der Druckzeit vorbelegt, und die Summe der Zeitanteile wird neben der Druckzeit angezeigt (nur als Hinweis). Die Menge (Teile pro Platte) trägt man immer ein; bei einer Datei ist sie vorbelegt, falls die Datei die Zahl liefert.
4. Je Position wählt man die Spule(n) aus Bambuddy (siehe „Spulen“ unten).
5. Optional Rüstzeit und Nacharbeit; Ausschussquote und Gewinnaufschlag kommen als Standard aus den Einstellungen und lassen sich ändern. Die Berechnung läuft live.
6. Gespeichert wird nur mit gewähltem Artikel oder gewählter Variante (sonst ist „Speichern“ gesperrt, eine Schnellkalkulation ohne Speichern bleibt möglich); die Datei wird mit der Kalkulation gespeichert. Später denkbar: einen Artikel direkt in der Kalkulation anlegen.

Gerechnet wird in dieser Reihenfolge. Standardwerte (Ausschussquote, Gewinnaufschlag, Arbeits-Stundensatz, Rundung) kommen aus den Einstellungen.

**Einheiten:** Eingaben in Gramm, Minuten, Watt und Prozent werden immer ausdrücklich umgerechnet: Prozentwert ÷ 100, Gramm ÷ 1000 → kg, Watt ÷ 1000 → kW, Minuten ÷ 60 → Stunden. Alle Zeiten (Druckzeit, Rüstzeit, Nacharbeit) gehen in Stunden in die Formeln ein.

1. **Druckkosten pro Platte** = Material + Strom + Maschine
2. **Druckkosten pro Stück** = Druckkosten pro Platte ÷ Teile pro Platte
3. **Druckkosten pro Stück inkl. Ausschuss** = Druckkosten pro Stück × (1 + Ausschussquote ÷ 100). Die Ausschussquote wirkt nur auf die Druckkosten.
4. **Arbeitszeit pro Stück** (optional, jedes leere Feld = 0):
   - in Stunden = (Rüstzeit pro Platte in Minuten ÷ Teile pro Platte + Nacharbeit pro Stück in Minuten) ÷ 60
   - als Kosten = Arbeitszeit pro Stück in Stunden × Arbeits-Stundensatz
5. **Selbstkosten pro Stück** = (3) + Arbeitskosten aus (4); angezeigt **ohne Arbeitszeit** (= (3)) und **mit Arbeitszeit**
6. **Verkaufspreis-Vorschlag** = Selbstkosten pro Stück (mit Arbeitszeit) × (1 + Gewinnaufschlag ÷ 100), gerundet
7. **Verkaufspreis** = der am Artikel hinterlegte Soll-Verkaufspreis, falls gesetzt (bei Varianten ggf. von der Vorlage geerbt, siehe Abschnitt 4); sonst der Verkaufspreis-Vorschlag (so auch bei einer Schnellkalkulation ohne Artikel)
8. **Gewinn pro Stück** = Verkaufspreis − Selbstkosten pro Stück (mit Arbeitszeit)
9. **Effektiver Stundenlohn** = (Verkaufspreis − Selbstkosten pro Stück ohne Arbeitszeit) ÷ Arbeitszeit pro Stück in Stunden. Ist die Arbeitszeit 0, wird „–“ angezeigt (keine Division).

| Baustein | Bezug | Berechnung | Werte |
| --- | --- | --- | --- |
| Material | pro Platte | Gramm ÷ 1000 × Preis/kg, eine Zeile pro Filament/Farbe | Gramm aus der `.gcode.3mf` (inkl. Spülmenge) oder von Hand; Preis je Zeile von der gewählten Bambuddy-Spule (eine bestellte Spule wird zuerst in Bambuddy angelegt); mehrere Spulen siehe „Spulen“ unten |
| Strom | pro Platte | Summe über die Materialien der Platte: Druckzeit des Materials in Stunden × Verbrauch in kWh pro Stunde × Strompreis pro kWh | Verbrauch je Druckermodell und Material aus der Tabelle des Modells (siehe „Druckermodell“ in Abschnitt 4), nicht mehr als Durchschnitt aus dem Archiv; Zeitanteil je Material aus der `.gcode.3mf` oder von Hand |
| Maschine | pro Platte | Druckzeit in Stunden × Maschinensatz | Maschinensatz = Stundensatz des gewählten Druckers + Trocknungsaufschlag (siehe unten) |
| Ausschussquote | auf Druckkosten | × (1 + Ausschussquote ÷ 100) | Standard aus Einstellungen |
| Rüstzeit (optional) | pro Platte | Minuten ÷ Teile pro Platte ÷ 60 × Arbeits-Stundensatz | z. B. Platte vorbereiten, Druck starten, abnehmen; leer = 0 |
| Nacharbeit (optional) | pro Stück | Minuten ÷ 60 × Arbeits-Stundensatz | z. B. Stützen entfernen, entgraten, verpacken; leer = 0 |
| Gewinnaufschlag | auf Selbstkosten | × (1 + Gewinnaufschlag ÷ 100) | Standard aus Einstellungen |

**Stundensatz pro Drucker:** Anschaffungswert ÷ (Nutzungsjahre × Druckstunden pro Jahr) × (1 + Wartungsaufschlag in % ÷ 100). Jeder Drucker hat seinen eigenen Satz. Alle drei Werte (Anschaffungswert, Nutzungsjahre, Druckstunden pro Jahr) sind Pflicht und müssen größer als 0 sein.

**Trocknung pauschal:** Die Trockner dienen allen Druckern, deshalb gibt es einen gemeinsamen Aufschlag:

- Jahreskosten eines Trockners = Anschaffungswert ÷ Nutzungsjahre × (1 + Wartungsaufschlag in % ÷ 100). Strom und Trocknungsstunden gehen nicht ein (Entscheidung des Maintainers: zu ungenau, weil Materialien und Spulenzahl wechseln).
- Trocknungsaufschlag pro Stunde = Summe der Jahreskosten aller aktiven Trockner ÷ Summe der Druckstunden pro Jahr aller aktiven Drucker.
- Maschinensatz in der Kalkulation = Stundensatz des Druckers + Trocknungsaufschlag. Deaktivierte Geräte zählen nicht mit. Gibt es keinen aktiven Trockner oder keinen aktiven Drucker, ist der Aufschlag 0.
- Beispiel: Trockner 132,99 € über 5 Jahre = 26,60 € pro Jahr; ein Drucker mit 1.000 Druckstunden pro Jahr ergibt 0,027 € pro Stunde Aufschlag.

**Restwert (nur Anzeige):** Anschaffungswert × (1 − Tage seit Anschaffung ÷ (365 × Nutzungsjahre)), zwischen 0 und dem Anschaffungswert (liegt das Anschaffungsdatum in der Zukunft, gilt der volle Anschaffungswert). Er fließt nicht in die Kalkulation ein.

**Strom des Druckers** wird nicht im Stundensatz berechnet, sondern als eigene Zeile aus den kWh des Drucks (Bambuddy).

**Verkaufspreis-Vorschlag:** wie in Schritt 6, immer aufgerundet (je nach Einstellung auf den Cent, ,10, ,50, den nächsten Preis mit ,90 am Ende oder ganze Euro); Kleinunternehmer-tauglich (ohne Umsatzsteuer). Angezeigt werden Selbstkosten **ohne und mit Arbeitszeit**, Verkaufspreis-Vorschlag, Gewinn pro Stück und effektiver Stundenlohn (Schritte 7–9).

**Spulen:** Die Kalkulation zeigt standardmäßig alle Spulen des gewählten Materials. Geschäftliche Spulen werden über das Bambuddy-Feld „Kategorie“ erkannt (Wort in den Einstellungen festlegbar); ist dort „nur geschäftliche Spulen anzeigen“ eingeschaltet, zeigt die Kalkulation nur diese, mit dem Schalter „alle Spulen“ im Formular. Ohne gewähltes Material und wenn keine Spule passt, steht ein Hinweis unter der Spulenwahl. Der verwendete Preis wird in der Kalkulation gespeichert. Pro Position (Material/Farbe) wählt man zuerst eine Spule. Ist ihr Restgewicht kleiner als die benötigten Gramm, erscheint eine weitere Zeile für eine weitere Spule: Die Gramm werden der Reihe nach verteilt (erste Spule bis zu ihrem Restgewicht, der Rest auf die nächste), der Preis wird je Spule gerechnet. Angenommen wird, dass die Spule leergedruckt wird. Deckt keine Spule das Restgewicht ab oder gibt es keine Spule mit passendem Material, bleibt die Position unvollständig und die Kalkulation lässt sich nicht speichern (es wird nie mit 0 gerechnet). Es gibt keinen Durchschnitt und keine freie Preiseingabe; möglicher späterer Ausbau (nur bei Bedarf): ein Kontrollkästchen „von Hand eingeben“.

**Werte aus Bambuddy:** Strompreis und Druckerliste haben einen Schalter „aus Bambuddy übernehmen“; gesetzt = abgeglichen und gesperrt, sonst eigener Wert.

## 7. Auswertung und Dashboard

- **Pro Artikel:** kalkulierte vs. tatsächliche Kosten pro Stück, Ø Verkaufspreis, Gewinn pro Stück (€ und %) und effektiver Stundenlohn – berechnet wie in Abschnitt 6 (Schritte 8 und 9), aber mit tatsächlichen Werten: echter Verkaufspreis und tatsächliche Produktionskosten statt Kalkulation (für „ohne Arbeitszeit“ werden die Arbeitskosten der Produktion herausgerechnet); produziert / verkauft / Bestand, Fehldruck-Anteil; Diagramm „Kosten pro Stück über die Zeit“; Warnung bei Abweichung über X % (Standard 15 %).
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

**Datei-Uploads:** Hochgeladene `.gcode.3mf`-Dateien (Kalkulation) haben ein Größenlimit und eine Typprüfung, werden nur in einem temporären Ordner ausgepackt und unter einem erzeugten Namen in `/data` abgelegt (nie unter dem Dateinamen des Nutzers). Ob die gespeicherten Dateien zur Sicherung gehören, wird in #182 entschieden (Empfehlung: ja).

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
- [x] 5b/6 Varianten und Lager als Pop-ups, Lager-Listen mit dem Tabellen-Baustein (#87)
- [x] 6/6 Übrige Seiten im neuen Stil, Vorlagen-Detail als Karte (#78)
- [x] Artikel-Dashboard nach dem Vorbild von ERPNext: Von der Übersicht springt man in die jeweilige Liste (Preise, Lagerbuchungen …). Die Kacheln im Artikel-Detail genügen (Entscheidung des Maintainers); Gruppen und „+“-Schnellanlage wie in ERPNext sind nicht nötig
- [x] Anpassung an Bambuddy: Farben des dunklen Themas, kompakte Schriftgrößen, flache Reiter-Leiste (auch in den Einstellungen), Formulare nicht über die volle Breite (#109, #111, #113)
- [x] Löschen und Bestätigen überall als Pop-up mit gemeinsamem Baustein (Gerät, Attribut, Vorlage, Artikel, endgültig löschen, Produktion stornieren, Zwei-Faktor zurücksetzen); Geräte-Tabelle mit Symbol-Buttons (#169)
- [x] Angleichung Oberflächen-Stil: Benutzer bearbeiten und Passwort zurücksetzen als Pop-up, Tabellen Merkmale und Varianten auf dem gemeinsamen Baustein (#171)
- [ ] Feinschliff: Logo, Diagramm-Optik, echte Ein/Aus-Schalter, weitere Akzentfarben (optional)

**M2 – Bambuddy-Anbindung und Produktion**

- [x] Bambuddy-Client: Archiv-Liste, Archiv-Detail, Spulen, Drucker, Strompreis; Einstellungen mit Adresse, Schlüssel und Verbindungstest (#117)
- [x] Seite Produktion: Drucke auswählen, Ergebnis-Artikel, Kostenaufteilung mit Vorschau, Fehldrucke, Ignorieren (#119, #127)
- [x] Produktion mit genau einem Ergebnis-Artikel, keine Kostenaufteilung (#140)
- [x] Produktion: Arbeitskosten erfassen (Arbeitszeit × Stundensatz, Teil der Gesamtkosten) (#134)
- [x] Produktion: Maschinenkosten = Druckstunden × Druckerstundensatz (zunächst ein Wert in den Einstellungen) statt Verschleiß aus Bambuddy (#135); der Einstellungswert ist mit #162 entfallen
- [x] Dashboard: Liste „Noch nicht zugeordnet“ (Drucke aus dem Bambuddy-Archiv, die weder in einer Produktion stehen noch ignoriert sind) (#119, #129)
- [x] Produktion: Druckzeit, Filament (g), Strom (kWh), Material und Farben pro Druck sowie Fehldruck-Stücke als Werte speichern (#148)
- [x] Artikel-Detail: Produktionen und Kosten pro Stück, Links zu Bambuddy (#120)

**M3 – Kalkulation**

- [x] Geräte und Stundensätze inkl. Trocknungspauschale (#157)
- [x] Geräte: Drucker aus Bambuddy übernehmen, Geräte ohne Produktion löschen (#163)
- [x] Rechenkern der Kalkulation (#159)
- [x] 3MF- und G-Code-Auslesen (#160)
- [x] Druckermodelle mit Stromverbrauch je Material, Drucker wählen ihr Modell (#174)
- [x] Druckermodelle: beliebig viele Zeilen, Handy-Anordnung, Übersicht je Material, Reihenfolge ändern (#177)
- [x] Produktion: Maschinensatz des Druckers aus den Geräten, Einstellungswert entfällt (#162)
- [ ] Kalkulation, Teil 1: von Hand rechnen und am Artikel speichern (#161)
- [x] Kalkulation, Teil 2: mit `.gcode.3mf`-Datei (#181)
- [ ] Kalkulation, Teil 3: Datei mit der Kalkulation speichern (#182)

**M4 – Verkauf und Auswertung**

- [ ] Verkauf von Hand
- [ ] Auswertung pro Artikel, pro Vorlage, gesamt; Dashboard vollständig
- [ ] CSV-Export

**M5 – Anbindung Rechnungs-Software (später)**

- [ ] Austauschbarer Anbindungs-Baustein
- [ ] RechnungsFee und/oder Haben

## 11. Offene Punkte

- ~~Reicht beim Bambuddy-Key das Recht „Status lesen“ für Archiv und Spulen?~~ Ja: Archiv, Spulen, Drucker und Einstellungen sind mit einem nur lesenden Key abrufbar (geprüft in #117).
- Liefern die APIs der Rechnungs-Software den Bezahlt-Status? (Prüfung zu Beginn von M5)
- Risiko: Bambuddy entwickelt sich sehr schnell → Anbindung in einem einzigen Baustein, Tests mit aufgezeichneten Antworten, Verbindungstest in den Einstellungen.
- ~~**Umfang der tatsächlichen Kosten** (M2/M4)~~ Entschieden (Maintainer): Eine Produktion erfasst Material-, Strom- und **Maschinenkosten** (Druckstunden × Druckerstundensatz; der Verschleiß aus Bambuddy entfällt, weil er schon im Druckerstundensatz steckt, #135) sowie **Arbeitskosten** (Arbeitszeit × Stundensatz, #134). **Zusatzteile** gibt es in MakerHub 3D nicht (Entscheidung des Maintainers, #159); weder Kalkulation noch Produktion berücksichtigen sie.
- ~~**Verkaufspreis bei einer Kalkulation an einer Vorlage** (M3)~~ Entfällt (Entscheidung des Maintainers, #180): Kalkulationen gibt es nur an Artikeln und Varianten, nie an Vorlagen.
- ~~**Druckerstundensatz mit Trocknung** (M3)~~ Entschieden (Maintainer): Satz pro Drucker plus gemeinsamer Trocknungsaufschlag, ohne Strom beim Trockner; Formeln in Abschnitt 6.
