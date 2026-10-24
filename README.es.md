# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

Núcleo Python para procesar conocimiento con trazabilidad: registros de origen, ID estables, contratos estructurados, tarjetas de segunda capa, candidatos, dependencias incrementales, controles por etapa y aceptación basada en evidencias. Los perfiles adaptan los contratos a cada dominio; la sincronización opcional con Dify apunta a documentos seleccionados explícitamente. Empieza con pruebas sintéticas y consulta --help antes de usar tus materiales.

## Requisitos

Python 3.10+; pytest (tests)

## Configuración y puntos de entrada

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## Instalación y uso

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## Alcance y limitaciones

Los perfiles usan rutas de ejemplo `./example-vault/<profile>` y dejan vacíos los ID remotos. Configura tus destinos; el endpoint Dify de ejemplo es http://127.0.0.1:5001/v1. No es un rastreador, redactor automático ni producto universal de deduplicación: los casos semánticos ambiguos necesitan revisión. Se excluyen tarjetas reales, materiales originales, el Skill privado y la interfaz de revisión Artifact SDK. PUBLIC-SCOPE.json enumera cuatro módulos de prueba dependientes del entorno no distribuidos y nueve casos privados omitidos explícitamente. Los servicios y modelos externos requieren configuración y validación propias.

## Registro de validación

2026-10-24 · Python: 264 ✓ / 9 skip

## Licencia y atribución

[MIT](LICENSE)

Son versiones de la documentación. Las interfaces, salidas CLI y ejecutables mantienen el comportamiento de la implementación actual.

[Referencia completa en chino](README.zh-CN.md) · [Política de mantenimiento de idiomas](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — Comunica problemas reproducibles con datos sintéticos. Elimina credenciales e información privada de los registros.
