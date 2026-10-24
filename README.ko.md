# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

출처를 추적할 수 있는 지식 가공용 Python 코어입니다. 출처 레코드, 안정적인 ID, 구조 계약, 2계층 카드, 지식 후보, 증분 의존성, 단계 게이트와 증거 기반 검수를 제공합니다. 프로필로 도메인을 바꾸고 선택적 Dify 동기화는 명시한 문서만 대상으로 합니다. 실제 자료 사용 전에 합성 테스트를 실행하고 각 CLI의 --help를 확인하세요.

## 필요 환경

Python 3.10+; pytest (tests)

## 설정 및 진입점

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## 설치 및 사용

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## 공개 범위와 제한

프로필의 `./example-vault/<profile>`은 데모 경로이며 원격 데이터셋/문서 ID는 비어 있습니다. Dify 예시 주소는 http://127.0.0.1:5001/v1입니다. 크롤러, 자동 글쓰기 또는 범용 중복 제거 제품이 아니며 의미 판단의 회색 영역은 검토가 필요합니다. 실제 지식 카드, 원작자 자료, 비공개 업무 Skill과 Artifact SDK 검토 UI는 제외합니다. PUBLIC-SCOPE.json에 제외된 환경 종속 테스트 모듈 4개와 명시적으로 건너뛴 비공개 통합 테스트 9개가 기록되어 있습니다. 외부 서비스와 모델은 별도 설정 및 검증이 필요합니다.

## 검증 기록

2026-10-24 · Python: 264 ✓ / 9 skip

## 라이선스 및 출처

[MIT](LICENSE)

프로젝트 문서의 언어별 버전입니다. 앱 화면, CLI 출력과 실행 파일은 현재 구현을 따릅니다.

[중국어 상세 참고 문서](README.zh-CN.md) · [언어 문서 유지 관리 정책](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — 재현 가능한 문제는 합성 데이터로 보고하고 로그의 자격 증명과 개인 정보를 제거하세요.
