# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

Noyau Python de traitement des connaissances avec traçabilité : fiches sources, identifiants stables, contrats structurés, cartes de deuxième couche, candidats, dépendances incrémentales, contrôles d'étape et validation fondée sur les preuves. Les profils adaptent les contrats au domaine ; la synchronisation Dify facultative vise des documents explicitement sélectionnés. Commencez par les tests synthétiques et consultez --help avant d'utiliser vos données.

## Prérequis

Python 3.10+; pytest (tests)

## Configuration et points d’entrée

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## Installation et utilisation

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## Périmètre et limites

Les profils contiennent les chemins d'exemple `./example-vault/<profile>` et des ID distants vides. Configurez vos cibles ; l'adresse Dify d'exemple est http://127.0.0.1:5001/v1. Ce n'est ni un robot de collecte, ni un rédacteur automatique, ni une solution universelle de déduplication : les ambiguïtés sémantiques nécessitent une revue. Les vraies cartes, œuvres sources, Skill privé et interface Artifact SDK sont exclus. PUBLIC-SCOPE.json décrit quatre modules de test dépendants de l'environnement non distribués et neuf cas privés explicitement ignorés. Les services et modèles externes demandent leur propre configuration et validation.

## Historique de validation

2026-10-24 · Python: 264 ✓ / 9 skip

## Licence et attribution

[MIT](LICENSE)

Il s’agit de versions de la documentation. Interfaces, sorties CLI et exécutables conservent le comportement de l’implémentation actuelle.

[Référence complète en chinois](README.zh-CN.md) · [Politique de maintenance des langues](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — Signalez les problèmes reproductibles avec des données synthétiques. Retirez identifiants secrets et informations privées des journaux.
