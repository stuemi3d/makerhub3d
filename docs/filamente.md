# Filamente und Spulen

Bambuddy kennt nur einzelne **Spulen**, jede mit eigenem Preis. MakerHub 3D fasst gleiche Spulen zu einem **Filament** zusammen, zum Beispiel „Bambu Lab · PLA Matte · Ivory White“. So siehst du je Sorte den Bestand, den Durchschnittspreis und den letzten Preis, und die Kalkulation kann mit diesen Preisen rechnen.

**Wichtig:** MakerHub 3D liest Bambuddy nur. Es ändert dort nie etwas: keine Spule, kein Preis, kein Gewicht. Spulen anlegen, wiegen und archivieren machst du weiterhin in Bambuddy.

## Ein Filament anlegen

1. Oben in der Navigation „Filamente“ öffnen.
2. „Neues Filament“ wählen.
3. Ausfüllen: Material ist Pflicht (z. B. PLA). Marke, Untertyp (z. B. Matte), Farbname und Farbcode sind optional. Der Name des Filaments wird daraus zusammengesetzt.
4. Optional: der **Standardpreis** (dein üblicher Einkaufspreis pro kg) und ein **Mindestbestand** (siehe unten).

Dasselbe Filament (gleiche Marke, gleiches Material, gleicher Untertyp, gleicher Farbname) gibt es nur einmal. Bearbeiten, Deaktivieren und Löschen gehen über die Symbole rechts in der Zeile. Löschen ist nur möglich, solange dem Filament keine Spulen zugeordnet sind; sonst bleibt nur „deaktivieren“.

## Spulen zuordnen

Eine Spule aus Bambuddy gehört erst zu einem Filament, wenn du sie zuordnest. Das geht schnell:

1. Auf der Seite „Filamente“ auf „Spulen zuordnen“ klicken. Die Zahl daneben zeigt, wie viele Spulen noch warten. Auch archivierte Spulen stehen in der Liste.
2. Für jede Spule steht ein **Vorschlag** da, entweder ein vorhandenes Filament oder „Neu anlegen“.
3. Mit „Bestätigen“ übernimmst du einen Vorschlag, mit „Alle bestätigen“ alle auf einmal. Prüfe die Liste vorher kurz.

Dabei gilt:

- Es wird nie geraten. Ist die Schreibweise anders („Gray“ statt „Grey“), schlägt MakerHub 3D kein vorhandenes Filament vor.
- Weicht nur die Marke ab („Bambu“ statt „Bambu Lab“), steht der Hinweis „Marke weicht ab“. Du entscheidest.
- Mehrere gleiche Spulen ohne Filament bilden zusammen ein neues Filament („Neu anlegen (wie #…)“).
- Fehlt einer Spule der Farbname, trägst du ihn selbst ein.
- Neue, noch nicht zugeordnete Spulen erscheinen als Hinweis, sobald du die Seite öffnest.

Beim Zuordnen merkt sich MakerHub 3D **Preis pro kg und Gewicht der Spule**. Diese Werte bleiben erhalten, auch wenn die Spule später in Bambuddy verschwindet.

## Die Spulen eines Filaments ansehen

In der Zeile des Filaments auf die Zahl bei „Spulen“ klicken. Du siehst je Spule: Zustand, Preis pro kg, Größe, Rest in Gramm, Zuordnungsdatum, zuletzt getrocknet, zuletzt verwendet, Notiz und (wenn vorhanden) Lagerort. Diese Angaben kommen aus Bambuddy. Über die Symbole rechts kannst du eine Spule einem **anderen Filament zuordnen** oder die **Zuordnung lösen**; sie erscheint dann wieder unter „Spulen zuordnen“. In Bambuddy ändert sich dabei nichts.

## Zustand einer Spule

| Anzeige | Bedeutung |
|---|---|
| aktiv | Die Spule ist in Bambuddy vorhanden. Ihr Restgewicht zählt zum Bestand. |
| archiviert | Die Spule ist in Bambuddy archiviert. Ihr Preis zählt für Durchschnitt und letzten Preis, das Restgewicht aber nicht zum Bestand. |
| nicht mehr in Bambuddy | Die Spule wurde in Bambuddy gelöscht. Der gespeicherte Preis bleibt im Durchschnitt, sie zählt nicht mehr zum Bestand. |
| unbekannt | Bambuddy antwortet gerade nicht. Es wird nichts als „gelöscht“ markiert; die gespeicherten Werte bleiben. |

**Tipp: Spulen in Bambuddy lieber archivieren als löschen.** Eine archivierte Spule behält alle ihre Daten (Marke, Farbe, Preis) und bleibt hier nachvollziehbar. Eine gelöschte Spule zeigt nur noch die gespeicherten Werte.

## Bestand und Mindestbestand

In der Tabelle steht je Filament der **Bestand in kg**: die Summe der Restgewichte aller aktiven Spulen. Dazu die Anzahl der Spulen, der Durchschnittspreis und der letzte Preis.

Trägst du beim Filament einen **Mindestbestand** ein und die Summe liegt darunter, erscheint „Unter Mindestbestand“. Ohne Mindestbestand gibt es keine Warnung. Der Mindestbestand gilt für das ganze Filament, nicht für einzelne Spulen.

## Wenn sich der Preis einer Spule in Bambuddy ändert

Nichts ändert sich still. Stimmt der Preis in Bambuddy nicht mehr mit dem gespeicherten überein, siehst du:

- auf der Seite „Filamente“ den Hinweis „… Spulen haben in Bambuddy einen anderen Preis“ mit dem Link „Jetzt prüfen“,
- am Filament einen Chip „Preis geändert (n)“,
- an der Spule den Hinweis „Preis geändert: gespeichert … €/kg, in Bambuddy … €/kg“.

Mit dem Symbol „Preis übernehmen“ ersetzt du den gespeicherten Wert durch den neuen. Erst dann rechnen Durchschnitt und letzter Preis mit dem neuen Preis. Die Übernahme steht im Änderungsprotokoll.

## Preisregeln in der Kalkulation

In der Kalkulation wählst du je Materialzeile ein Filament und eine **Preisregel**:

| Regel | Was sie rechnet |
|---|---|
| Durchschnitt | Der Preis pro kg aller zugeordneten Spulen, nach Gramm gewichtet (große Spulen zählen mehr). |
| Letzter Preis | Der Preis der zuletzt zugeordneten Spule. |
| Standardpreis des Filaments | Dein üblicher Einkaufspreis, den du beim Filament eingetragen hast. Nicht zu verwechseln mit dem Standardpreis aus den Bambuddy-Einstellungen. |
| Einzelne Spule | Der Preis einer bestimmten Spule aus Bambuddy. |

Welche Regel eine neue Zeile vorschlägt, stellst du unter „Einstellungen“ ein. Die gewählte Regel, der Preis pro kg und die Gramm werden **mit der Kalkulation gespeichert**. Ändert sich ein Preis später, bleiben gespeicherte Kalkulationen gleich. Willst du neu rechnen, kopierst du die Kalkulation. Fehlt ein Preis, bleibt die Zeile unvollständig. Es wird nie mit 0 gerechnet.

## Produktion rechnet mit dem echten Rollenpreis

Bei einer Produktion zählt immer der Preis, den Bambuddy für die tatsächlich verwendete Rolle liefert, nicht der Standardpreis des Filaments. Dieser Wert wird bei der Buchung gespeichert.

Hat eine Spule in Bambuddy keinen Preis, rechnet Bambuddy selbst mit seinem Standardpreis. Dann erscheint bei der Produktion der Hinweis, dass hier vermutlich der Standardpreis gerechnet wurde. Ist die Spule wirklich kostenlos, trägst du im Feld „Material“ 0 ein.

So kannst du in der Auswertung vergleichen, was die Kalkulation erwartet hat (z. B. Standardpreis) und was das Material wirklich gekostet hat. Der Unterschied ist dein Einkaufsvorteil.
