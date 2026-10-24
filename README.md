# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

A traceable knowledge-processing Python core: source records, stable IDs, structured contracts, Layer-2 cards, candidate knowledge, incremental dependencies, stage gates and evidence-based acceptance. Profiles adapt the contracts to different domains; optional Dify synchronization targets explicitly selected documents. Start with synthetic tests and inspect each CLI's --help before using your own material.

## Requirements

Python 3.10+; pytest (tests)

## Configuration and entry points

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## Installation and usage

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## Scope and limitations

Profiles contain `./example-vault/<profile>` demonstration paths and empty remote dataset/document IDs; configure your own targets first. The sample Dify endpoint is http://127.0.0.1:5001/v1. This is not a crawler, automatic writer or universal deduplication product: semantic gray areas need review. Real knowledge cards, original creator materials, the private business Skill and Artifact SDK review UI are excluded. PUBLIC-SCOPE.json lists four omitted environment-bound test modules and nine explicitly skipped private integration cases. External services and models require separate setup and validation.

## Validation record

2026-10-24 · Python: 264 ✓ / 9 skip

## License and attribution

[MIT](LICENSE)

These are documentation editions. Application interfaces, CLI output and executable releases retain the behavior of the current implementation.

[Complete Chinese reference](README.zh-CN.md) · [Language maintenance policy](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — Report a reproducible issue with synthetic data. Remove credentials and private information from logs.
