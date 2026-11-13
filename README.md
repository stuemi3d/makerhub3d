# MakerHub 3D

> **⚠️ In Entwicklung – noch nicht für den produktiven Einsatz.**

MakerHub 3D ist ein kleines, selbst gehostetes Tool für 3D-Druck-Kleingewerbe. Es schließt die Lücke zwischen [Bambuddy](https://github.com/maziggy/bambuddy) (Druckerverwaltung) und einer Rechnungs-Software: **Artikel, Kalkulation, Produktion, Lager und Auswertung**. Es läuft als eigener Docker-Container und wird über einen „External Link“ in die Bambuddy-Seitenleiste eingebunden.

## Plan

Ziele, Umfang, Technik und Meilensteine stehen in [docs/plan.md](docs/plan.md).

## Erster Start

Kein Standardpasswort: Den ersten Admin legst du beim ersten Start mit einem Einrichtungscode aus dem Container-Protokoll an. Anleitung: [docs/erster-start.md](docs/erster-start.md).

## Entwickeln

Voraussetzung: [uv](https://docs.astral.sh/uv/). uv installiert Python 3.12 und alle Abhängigkeiten in den festen Versionen aus `uv.lock`.

```sh
uv sync                                  # Abhängigkeiten installieren
uv run uvicorn app.main:app --reload     # App starten, dann http://127.0.0.1:8000 öffnen
                                         # (Einrichtungscode für den ersten Admin steht in der Ausgabe)
uv run pytest                            # Tests (ohne Browser-Tests)
uv run playwright install chromium       # einmalig: Browser für die Browser-Tests
uv run pytest -m browser                 # Browser-Tests (Desktop und Handy)
uv run ruff check                        # Codestil prüfen
uv run ruff format --check               # Formatierung prüfen
```

## Lizenz

[GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE)
