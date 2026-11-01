# factory-eval/v1 接続契約

本体とは標準入出力のJSONLで接続します。モデルキー・作業トークンは `EVAL_MODEL_KEY` / `EVAL_WORK_TOKEN` として子プロセスにだけ渡します。評価リポジトリの制御トークンは渡しません。標準エラーは成果物に記録しません。

すべてのコマンドとイベントに `version: "factory-eval/v1"` を付けます。

| 入力action | 用途 |
|---|---|
| hello | real_runner / native_ci / pre_call_budget / draft_pr / followup の能力確認 |
| start | 固定作業先・初期SHA・依頼・モデル・変更範囲・上限で本体を開始 |
| approve | 範囲内の計画を承認。質問があれば停止 |
| amend | 範囲内の追加ファイルだけを承認 |
| followup | readyの公開前変更に追加依頼を与える。E03で使用 |
| publish | readyの差分を本体のPublish経路でDraft PRへ書き込む |
| check_ci | 本体の通常GitHub読み取り経路で同じコミットのCIを再確認 |
| fix_ci | 本体のStartCIFix経路で元の要求と失敗情報を保持して再計画 |

`start` は `run_id`（24桁のランダム16進数）、`task_id`、`repository`、`base_commit`、`base_branch`、`request`、`provider`、`model`、`allowed_paths`、`forbidden_paths`、`max_files`、`max_bytes`、`seconds`、任意の `pull_number` を受け取ります。

| 出力type | 内容 |
|---|---|
| reserve | 物理モデルHTTPリクエスト直前のreservation_id・入力上界・最大出力。親の `{"allow": true}` が必要 |
| write_intent | GitHubの各書き込み直前。親の停止・時間上限確認後の承認が必要 |
| usage | プロバイダーから得た実トークン数、usage_known、usage_id。正常な単一リクエストのみsettle=trueとreservation_idを付与して差額精算する。エラー・複数試行・識別子欠落は精算しない |
| core_diagnostic | 形式拒否・編集適用拒否・再試行の固定コード、フェーズ、操作有無、step/file_index/edit_index/match_count/attempt/retry_count。任意の本文・パス・生ログは渡さない |
| checkpoint | 本体Runnerが発行したチェックポイント、計画、AIの受け入れ条件、未確認事項 |
| result | implementation、生成changes、metrics、サニタイズされたerrorコード |

`changes` にはpath・create/update/delete・content・SHA-256・base_commitを含めます。新規ファイルの追加依頼前後は別に保存し、最終内容だけではE03を合格にしません。計画と追加提案は変更範囲を検証し、実際のPublishの直前にも全差分を再検証します。

資格情報を含む変更を拒否し、親もイベント・成果物に対して秘密値、認証ヘッダー、既知のキーパターン、クエリ付きURLをマスクします。プロバイダーの生リクエストは保存しません。

採点証拠の識別子は repository、commit、definition_sha、run_id、issuer（GitHub App ID）、workflow_run_id です。必須のbuild・requirements・regressionの3チェックはすべて同じ識別子に対応し、GitHub APIから取得した発行元とdetails URLを照合します。

本体に未対応の機能がある場合は `ci_capability_unavailable` として停止します。評価のために本体の生成やCI修正を別の実装で代替しません。

評価制御側は、`request` に依頼と同じ許可・禁止パスおよび変更量の上限を明記します。`requests/<index>.json` は実際に送信した文だけを送信順に保存し、後続依頼を先にモデルへ渡しません。E03の追跡検査は追加前のFormatSummary未実装、追加後の実装と内容変更、および両時点の未公開状態を必須にします。
