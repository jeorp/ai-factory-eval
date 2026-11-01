# 実行・設定手順

## 1. 課金のない検証

```sh
python3 -m unittest discover -s tests -v
python3 -m factory_eval self-test
python3 -m factory_eval validate --config examples/config.json
```

`examples/config.json` は形式の例です。SHA・モデル・App ID・単価は実値に置き換えてください。例の単価は料金表ではありません。プロバイダーの単価、通貨、適用日を実行者が固定します。設定にAPIキーやトークンの値を記載しないでください。

## 2. 専用作業リポジトリ

課題ごとに非公開の空の専用作業リポジトリを用意します。本番、本体、評価定義のリポジトリは対象にできません。設定の `dedicated: true` と Actions の許可リストを両方指定します。

固定初期状態を公開するときは `export-task` を使います。参考解答と採点コードは出力されません。

```sh
python3 -m factory_eval export-task E01 --output /tmp/factory-e01
```

作業リポジトリのデフォルトブランチには空のアンカーコミットを置き、その子の `fixture/E01-v1` などのブランチに、出力した初期ファイルだけをコミットしてください。初期コミットを `work_repositories.E01.initial_sha` に設定します。初期コミットの木は固定fixtureと完全一致する必要があり、余分な `.github/` やREADMEの変更も事前検査で拒否します。

E04は、初期の失敗を採点したあと、実行専用ブランチからデフォルトブランチに Draft PR を作成し、そのPRを対象に本体の通常CI修正経路から計画を開始します。初期コミットとデフォルトブランチが同じだと空のPRになるため、このアンカー構成が必要です。

作業先にワークフロー定義を置く必要はありません。信頼済みの `ai-factory-eval/.github/workflows/grade.yml` が別リポジトリの正確なコミットを採点し、作業先へチェックを報告します。

## 3. GitHub App、権限、変数

評価用 GitHub App を評価リポジトリと専用作業リポジトリにインストールします。評価定義の変更権限は与えず、必要なスコープだけを発行してください。

| 用途 | 必要な権限・制約 |
|---|---|
| 評価制御トークン | 評価リポジトリの Actions read/write、Contents read。インストールトークンを使用 |
| チェック報告トークン | 専用作業リポジトリの Checks write、Contents read。報告ジョブでAppから発行 |
| 本体用作業トークン | 専用作業リポジトリの Contents / Pull requests write、Actions / Checks read、および本体の通常GitHub MCPに必要なアクセス |
| 初期コードのcheckout | 専用作業リポジトリの Contents read のみ |
| 本体のcheckout | 本体の Contents read のみ |

リポジトリの Variables を設定します。

- `EVAL_WORK_REPOSITORIES`: 完全なリポジトリ名の JSON 配列。例: `["jeorp/ai-factory-eval-work-e01", "jeorp/ai-factory-eval-work-e04"]`
- `EVAL_WORK_REPOSITORIES_CSV`: 同一owner配下のリポジトリ短名のカンマ区切り。例: `ai-factory-eval-work-e01,ai-factory-eval-work-e04`

`evaluation` Environment に `EVAL_APP_ID`、`EVAL_APP_PRIVATE_KEY`、`EVAL_MODEL_KEY`、`EVAL_WORK_TOKEN`、`CORE_READ_TOKEN` を設定します。`EVAL_READ_TOKEN` は `grade` ジョブのcheckoutで使うためリポジトリSecretに設定します。読み取り専用トークンです。Environment の承認者を設定する場合は、手動比較の制御ジョブとチェック報告ジョブの実行時にその承認が必要です。

通常の CI ジョブはこれらの資格情報を参照しません。採点コンテナに環境変数やトークンを渡しません。Docker socket も渡しません。評価制御ジョブでは生成コードを実行しません。

## 4. SHAの固定と本体ビルド

評価コードのコミット後、その SHA に固定したタグ（例 `eval-pinned-v1`）を作り、`definition_sha` と `workflow_ref` を設定します。実行時の設定JSONはリポジトリ外のファイルに置いてください。自分自身のコミットSHAを設定ファイルに埋め込む必要はありません。

`workflow_dispatch` の起動先はブランチまたはタグで指定するため、ハーネスは起動前にその参照が固定SHAを指すことを確認し、採点側でも `GITHUB_SHA` と照合します。[GitHubの仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)

`baseline_sha` と `candidate_sha` は本体の完全なコミットSHAです。`core_path` にその両コミットが存在する本体のローカルGitチェックアウトを指定します。本体の作業ツリーをコピーせず `git archive <SHA>` した内容だけをビルドします。

```sh
python3 -m factory_eval build-core \
  --source /path/to/ai-factory \
  --sha FULL_40_CHARACTER_SHA \
  --output /tmp/core-evaluation-build
```

`cmd/factory-eval` を既に持つ本体の版は、衝突を防ぐため上書きせず拒否します。現在の入口は、既存の `StartImplementation`、`Runner.Run`、プロバイダー、GitHub Clientを利用します。ネイティブCI機能を持つ版では `StartCIFix` を呼び、古い版ではライブ評価を `blocked` にします。評価のために異なるプロンプトを注入することはありません。

## 5. 初回スモークと比較

最初は `tasks: ["E01"]`、`trials: 1` と対応する `work_repositories` だけを選びます。スモークもbaseline/candidateを1回ずつ実行します。その後、4課題・3試行へ広げます。同時実行数は1です。

ローカル制御では環境変数を安全なシェルや資格情報管理から注入し、次を実行します。

```sh
python3 -m factory_eval run \
  --config /tmp/factory-config.json \
  --output results/comparison-001
```

GitHub Actionsでは **Live model comparison (manual, paid)** の `config` 入力に設定JSONを渡します。`core_path` はワークフローがチェックアウト先へ置き換えます。モデル・作業先・単価・SHA・予算は実行者が明示します。GitHub Appトークンの有効時間と成果物保存時間を考慮し、Actionsの全体時間上限は2700秒以下に制限します。

全試行と初期状態確認が予算内に収まらない場合は、残りの試行も `budget_exceeded` として記録します。試行を集計から取り除きません。

## 6. 上限と費用

`trial_limits` と `total_limits` の呼び出し数、入力・出力トークン、時間、CI試行数、費用をすべて指定します。

- モデル呼び出し直前に、システムプロンプトを含む送信JSONのバイト数を入力トークンの保守的な上界として予約します。出力は本体が設定した最大出力トークンを予約します。生成設定そのものは変えません。
- 正常終了した単一の物理リクエストと使用量をIDで対応付けられた場合だけ、予約を実トークン数に精算し差額を戻します。エラー・再試行・使用量不明・旧アダプターの識別不能な呼び出しは全額予約を残します。重複した精算は一度だけ適用します。
- `reserved_cost` は精算済み使用額＋未精算予約額の上界、`gross_reserved_cost` は予約総額、`released_cost` は精算で解放した差額です。使用量が予約上界を超えた場合は差額を解放せず停止します。イベントは送信許可前と精算前に保存します。プロセス中断後の自動再開・再精算は行わず、既存出力先への再実行も拒否します。
- HTTPエラー・再試行も物理リクエストごとに予約します。実利用トークンは本体プロバイダーから別途記録します。取得できない場合は `usage_unavailable_calls` に残し、ゼロ課金と解釈しません。
- 費用は設定単価による `estimated_cost` と保守的な `reserved_cost` を区別します。請求額は取得していないため `provider_billed_cost: null` です。
- `trial_limits.ci_attempts` は生成コミットの採点回数（最大3、CI修正最大2回）。初期状態の検証は別に1回記録します。`total_limits.ci_attempts` は初期検証を含む全ワークフロー数の上限です。
- 各GitHub書き込みの直前も停止状態を確認します。取消後は新しいモデル呼び出し・書き込み・自動後処理を始めません。

## 7. 結果と後処理

全試行に対する達成率、採点できた試行に対する達成率、初回CI成功率、固定要求別の件数、回帰、誤った完了判定、静的修正・CI修正、利用量を出力します。モデルの版が取得できない場合は `model_version: null` とします。E01の契約が通っても自由文の言語品質を保証したとは報告しません。

誤った完了判定の母数は、本体が静的検証を終えて公開可能（`ready`）とした各回の採点対象です。後からCI失敗を認識・修正しても、その回の公開可能判定に未達があれば数えます。本体のCI認識は別の `core_ci_state` に保存します。課題の版・指紋・生成条件が異なる成果物を混ぜた場合は、比較無効と表示します。

`cleanup: true` の場合、実行IDに対応するDraft PR・ブランチだけを、現在のコミットと所有権を照合して閉じ、削除します。デフォルトブランチ、移動したブランチ、別のPRには作用しません。取消や後処理の失敗では `cleanup_pending: true` を残します。PR作成前にブランチだけ作られた場合も `branch_created` を記録します。

`checkpoint.json` は最後に保存した本体チェックポイントです。E03の追加依頼とCI修正は同じヘッドレスプロセスのキャッシュ・状態を継続利用します。プロセスを失った実行を新しい予算で自動再送するCLIは提供しません。中断した試行はその分類で保存し、PR/ブランチを確認後、新しい独立試行を開始してください。

成果物から比較を再生成できます。モデルを呼ぶことはありません。

```sh
python3 -m factory_eval report results/comparison-001
```

## 8. 検証の範囲

通常CIは基盤の単体・契約テスト、および全4課題の初期・参考解答・破損の自己検証です。参考解答の成功は実モデルの成績ではありません。ライブの成功には、設定済みの専用作業リポジトリ、対応する本体コミット、モデル資格情報、評価用App、明示した予算を揃えて実評価する必要があります。

## E01 v2 の採点変更

E01 v2 は、従来の5要求を維持し、READMEにある未指定言語のja既定値を `E01-R06` として追加します。言語省略リクエスト、その保存・再読込、旧保存データについて全5呼び出しを検査し、既存動作の回帰検査にも含めます。初期コードと参考解答ではこの要求が成立します。依頼、初期コード、参考解答は変更しません。v1の過去スコアを上書きせず、v2の指紋・評価定義SHAで再評価してください。
