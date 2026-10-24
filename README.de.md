# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

Python-Kern für nachvollziehbare Wissensverarbeitung: Quelldatensätze, stabile IDs, strukturierte Verträge, Karten der zweiten Ebene, Wissenskandidaten, inkrementelle Abhängigkeiten, Stufenprüfungen und belegbasierte Abnahme. Profile passen die Verträge an Fachgebiete an; die optionale Dify-Synchronisierung betrifft ausdrücklich ausgewählte Dokumente. Zuerst synthetische Tests ausführen und vor eigenen Daten die jeweilige --help lesen.

## Voraussetzungen

Python 3.10+; pytest (tests)

## Konfiguration und Einstiegspunkte

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## Installation und Verwendung

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## Umfang und Einschränkungen

Profile enthalten Beispielpfade `./example-vault/<profile>` und leere externe IDs. Eigene Ziele zuerst konfigurieren; die Dify-Beispieladresse ist http://127.0.0.1:5001/v1. Dies ist kein Crawler, automatischer Autor oder universelles Deduplizierungsprodukt. Semantische Grenzfälle brauchen Prüfung. Echte Wissenskarten, fremde Originalmaterialien, der private Skill und die Artifact-SDK-Prüfoberfläche sind ausgeschlossen. PUBLIC-SCOPE.json nennt vier nicht veröffentlichte umgebungsgebundene Testmodule und neun ausdrücklich übersprungene private Integrationsfälle. Externe Dienste und Modelle separat einrichten und prüfen.

## Prüfstand

2026-10-24 · Python: 264 ✓ / 9 skip

## Lizenz und Herkunft

[MIT](LICENSE)

Dies sind Sprachfassungen der Dokumentation. Oberflächen, CLI-Ausgaben und ausführbare Pakete folgen weiterhin der aktuellen Implementierung.

[Vollständige chinesische Referenz](README.zh-CN.md) · [Pflege der Sprachfassungen](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — Reproduzierbare Probleme mit synthetischen Daten melden. Zugangsdaten und private Informationen aus Protokollen entfernen.
