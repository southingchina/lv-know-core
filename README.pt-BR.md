# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

Núcleo Python para processamento rastreável de conhecimento: registros de origem, IDs estáveis, contratos estruturados, cartões de segunda camada, candidatos, dependências incrementais, controles de etapa e aceitação baseada em evidências. Perfis adaptam os contratos a cada domínio; a sincronização opcional com Dify usa documentos selecionados explicitamente. Comece pelos testes sintéticos e consulte --help antes de usar seus materiais.

## Requisitos

Python 3.10+; pytest (tests)

## Configuração e pontos de entrada

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## Instalação e uso

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## Escopo e limitações

Os perfis usam caminhos de exemplo `./example-vault/<profile>` e IDs remotos vazios. Configure seus destinos; o endpoint Dify de exemplo é http://127.0.0.1:5001/v1. Não é um coletor, redator automático ou produto universal de deduplicação: ambiguidades semânticas precisam de revisão. Cartões reais, materiais originais, Skill privado e interface Artifact SDK foram excluídos. PUBLIC-SCOPE.json lista quatro módulos de teste dependentes do ambiente não distribuídos e nove casos privados explicitamente ignorados. Serviços e modelos externos exigem configuração e validação próprias.

## Registro de validação

2026-10-24 · Python: 264 ✓ / 9 skip

## Licença e créditos

[MIT](LICENSE)

São versões da documentação. Interfaces, saídas CLI e executáveis mantêm o comportamento da implementação atual.

[Referência completa em chinês](README.zh-CN.md) · [Política de manutenção dos idiomas](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — Relate problemas reproduzíveis com dados sintéticos. Remova credenciais e informações privadas dos logs.
