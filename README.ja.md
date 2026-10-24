# LV-Know Core

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

出典を追跡できる知識加工用 Python コアです。出典レコード、安定 ID、構造化契約、二層カード、知識候補、増分依存、段階ゲート、証拠に基づく検収を扱います。プロファイルで分野を切り替え、任意の Dify 同期は明示した文書だけを対象にします。自分の資料を扱う前に合成テストを実行し、各 CLI の --help を確認してください。

## 必要な環境

Python 3.10+; pytest (tests)

## 設定とエントリーポイント

`profiles/`, `scripts/`, `references/`, `assets/`, `PUBLIC-SCOPE.json`, `http://127.0.0.1:5001/v1`

## 導入と使用方法

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```


## 公開範囲と制限

プロファイルの `./example-vault/<profile>` はデモ用で、外部データセット/文書 ID は未設定です。Dify のサンプル接続先は http://127.0.0.1:5001/v1 です。クローラー、自動執筆、万能な重複排除製品ではなく、意味判断の曖昧な部分には確認が必要です。実知識カード、著作者の原資料、私用業務 Skill、Artifact SDK のレビュー UI は含みません。PUBLIC-SCOPE.json に環境依存で除外したテスト 4 モジュールと、明示的にスキップする私用統合テスト 9 件を記載しています。外部サービスやモデルは別途設定・検証してください。

## 検証記録

2026-10-24 · Python: 264 ✓ / 9 skip

## ライセンスと出典

[MIT](LICENSE)

これらは文書の言語版です。アプリ画面、CLI 出力、実行ファイルは現在の実装に従います。

[中国語の詳細リファレンス](README.zh-CN.md) · [言語版の保守方針](docs/LOCALIZATION.md)

[Issues](https://github.com/southingchina/lv-know-core/issues) — 再現できる問題は合成データで報告し、ログの認証情報や個人情報を除いてください。
