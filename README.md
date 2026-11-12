# MakerHub 3D

> **⚠️ In Entwicklung – noch nicht für den produktiven Einsatz.**

MakerHub 3D ist ein kleines, selbst gehostetes Tool für 3D-Druck-Kleingewerbe. Es schließt die Lücke zwischen [Bambuddy](https://github.com/maziggy/bambuddy) (Druckerverwaltung) und einer Rechnungs-Software: **Artikel, Kalkulation, Produktion, Lager und Auswertung**. Es läuft als eigener Docker-Container und wird über einen „External Link“ in die Bambuddy-Seitenleiste eingebunden.

## Plan

Ziele, Umfang, Technik und Meilensteine stehen in [docs/plan.md](docs/plan.md).

## Entwickeln

Voraussetzung: [uv](https://docs.astral.sh/uv/). uv installiert Python 3.12 und alle Abhängigkeiten in den festen Versionen aus `uv.lock`.

```sh
uv sync                                  # Abhängigkeiten installieren
uv run uvicorn app.main:app --reload     # App starten, dann http://127.0.0.1:8000 öffnen
uv run pytest                            # Tests
uv run ruff check                        # Codestil prüfen
uv run ruff format --check               # Formatierung prüfen
```

## Lizenz

[GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE)
