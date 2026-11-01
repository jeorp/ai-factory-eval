# AI Factory Eval

AI Factory の実 Runner を、固定した課題・独立したテスト・実際の GitHub CI で評価する基盤です。設計の原文は [REQUIREMENTS.md](REQUIREMENTS.md)、実行手順は [docs/operations.md](docs/operations.md) にあります。

Python 3.12、Go 1.27.2、Node.js 24.10.0 を使用します。Python の外部ランタイム依存はありません。

```sh
python3 -m factory_eval validate
python3 -m unittest discover -s tests -v
python3 -m factory_eval self-test
```

これらのコマンドは実モデルを呼びません。`self-test` が実行するのは、このリポジトリで管理する合成コード・参考解答・意図的な破損だけです。

| 課題 | 主な採点対象 |
|---|---|
| E01 | 英語プロンプト、UI→API→保存→5種類のAI呼び出しの出力言語契約 |
| E02 | 任意フィールド追加、型・利用側・変換・保存・旧形式との互換性 |
| E03 | 新規ファイル、公開前の追加依頼、初回内容とcreate操作の保持 |
| E04 | 4種類のCI失敗の修正、既存テストの保護、新たなビルド破損の検出 |
| E05 | 順序付き送金バッチの原子性、重複ID排除、失敗時の状態保持 |
| E06 | 注文合計の修正、整数境界・入力保持・固定シード200注文での整合性 |
| E07 | 並行送金、失敗時の完全な状態保持、ID再試行・台帳の独立性・race detector |

各課題は `tasks/E*/task.json`、`initial/`、`reference/`、`grading/` で構成します。AI に渡すのは `initial/` と依頼だけです。採点項目と採点コードは実行前に固定し、AI の条件一覧から独立させます。E01 の自由文の言語品質は未測定です。

実評価では [examples/config.json](examples/config.json) のプレースホルダーを埋め、全 SHA、モデル、専用作業先、単価と予算、資格情報の環境変数名を指定します。

```sh
python3 -m factory_eval run --config /tmp/factory-config.json --output results/comparison-001
```

baseline / candidate を各課題で交互に、既定3回ずつ実行します。通常の push / PR CI は無料の基盤テストのみで、課金のある比較は **Live model comparison (manual, paid)** から手動起動します。

`integrations/ai-factory/` は、本体の固定 SHA をアーカイブした一時チェックアウト内でビルドする小さなヘッドレス入口です。本体の Runner・プロンプト・生成処理は複製しません。本体の作業ツリーに未コミットの変更があっても、それを指定 SHA の実装として取り込みません。

現在確認した本体コミット `cb12fc9462c9668381f6af4c6cd038213cc9eb8f` では入口のビルドが可能ですが、ネイティブCI読み取り・修正機能が未対応です。その版のライブ評価は `blocked` として記録します。実評価には本体の `StartCIFix` / `checking_ci` 対応を含むコミットが必要です。

生成コードは、信頼済み評価ワークフローからネットワーク・資格情報のないコンテナで実行します。ホストが生の実行結果を解析し、別のジョブが評価用 GitHub App として正確なコミットへチェックを発行します。発行元・実行ID・ワークフロー実行・コミットが一致しない結果は採用しません。

計測条件を修正した課題版は E01 v3 / E02〜E04 v2 です。全依頼で変更範囲を明示し、E03は追加依頼の先読みを防いで実際の内容変更を採点します。旧結果は上書きせず、新しい評価SHAとE03の初期SHAで再実行します。詳細は [実行手順](docs/operations.md) を参照してください。

実行成果物は `requests/`、`run.json`、`checkpoint.json`、`events.jsonl`、`changes/`、`grading.json`、`ci/`、`report.md`、比較全体の `comparison.json` / `usage.json` です。既定保存期間は30日です。

E06・E07 v2は要求を明確化して採点を強化しています。初期コードと参考解答はv1から変更していません。過去のv1の結果は保持し、同条件の比較には含めません。E07の `TestRace` は `go test -race` とCコンパイラを使用します。詳細は実行手順を参照してください。
