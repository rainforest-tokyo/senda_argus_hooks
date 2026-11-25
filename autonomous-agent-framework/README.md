# Senda Autonomous Agent Framework

LLMが目的とゴールをもとに作業手順を立て、MCPやSQLiteから情報を集め、蓄積した根拠でゴールを判定するPythonフレームワークです。LLMとのやり取りにはLangChain、各周回の実行にはLangGraph、進捗と監査イベントにはSenda-Argus Hooksを使います。

- プランナーはワーカーが使えるAPIと検索条件の一覧を受け取り、実行順、引数、期待するデータをJSONで返します。
- ワーカーはステップを順に実行してファクトを保存します。精査後は作業の続行、個別条件の確認、ゴール判定、再計画を選びます。
- ゴール判定は同じ実行の全周回のファクトを参照します。未達の場合は不足している根拠と理由を返し、計画の修正または組み直しを依頼します。

## ローカルOllamaで実行

Python 3.11以降と、Ollamaに登録した `qwen3-coder:30b` を使います。このディレクトリで実行してください。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ../python -e '.[mcp,mongo,test]'

senda-agent import --db .local/knowledge.sqlite examples/documents
senda-agent run --config examples/ollama.json \
  --purpose '社内案内の確認' \
  --goal 'SQLite RAGで「営業時間」を検索し、サンプルカフェの平日の営業時間を根拠付きで確認する。' \
  --criterion '取得したファクトで平日の開始・終了時刻を確認できる。'
```

接続先は `http://127.0.0.1:11434/v1`、モデルは `qwen3-coder:30b` です。サンプルではOllamaのOpenAI互換認証用にローカル接続で `ollama` を渡します。別の接続先を使う場合は `base_url`、`model`、`api_key_ref` を設定してください。秘密情報の参照は `env:変数名` 形式です。`llm.overrides` の `planner`、`worker`、`goal_judge` で、役割ごとにモデルや `system_prompt` を上書きできます。設定内の相対パスは実行時のカレントディレクトリから解決します。

`max_replans` の既定値は `-1` です。`0` は初回計画のみ、正の整数はワーカーとゴール判定からの再計画を合わせた上限です。Ctrl-Cで実行を中止できます。CLIの終了コードは達成時が `0`、再計画上限による未達が `2`、エラーが `1`、中止が `130` です。

## 情報源とファクト

`llm.default.rag` にSQLiteの `path` と一回の最大取得件数 `limit` を指定します。取り込みはYAML、プレーンテキスト、Markdownに対応しています。YAMLは `id` と `info` 内の `name`、`description`、`severity`、`tags` を取り込み、元ファイルのパスを出典にします。SQLiteの `documents` テーブルには `id`、`title`、`body`、`source`、`metadata` が入ります。

プランナーに渡す検索定義には、SQLiteに存在するタグや重要度と比較方法が含まれます。検索語はそれぞれタイトルまたは本文への部分一致で照合し、メタデータ条件を合わせられます。ファクトは `facts_dir` 内の実行IDごとのJSONLへ追記され、取得時刻、周回、出典、取得内容を記録します。

`llm.default.mcp` は情報収集用のMCPサーバー一覧です。各要素に `name`、`transport`、`allowed_tools` を設定します。`stdio` には `command` と必要に応じて `args`、`streamable_http` には `url` を指定します。必要に応じて `api_key_ref` を設定できます。プランナーへの公開対象は許可リストにあり、サーバーが `readOnlyHint=true` として公開するツールです。

## 進捗とMongoDB

進捗は標準出力に一行ずつ表示します。MongoDBにも保存する場合は、設定の最上位に `mongo` オブジェクトを追加し、接続URIを指定した環境変数に設定します。

```json
{
  "uri_ref": "env:SENDA_MONGODB_URI",
  "database": "senda_agent",
  "collection": "agent_events",
  "retries": 2,
  "timeout_ms": 2000
}
```

MongoDBは運用中の進捗イベントをバックグラウンドで保存します。イベントIDを `_id` に使い、再試行で同じドキュメントの追加を防ぎます。MongoDBの保存失敗は標準エラー出力で確認できます。LLMやMCP呼び出しを含むHookイベントは、サンプルの `hook_exporters` 設定で `.local/hooks.jsonl` に保存します。進捗、ファクト、Hookイベントは共通の `run_id` で照合できます。

## Pythonから実行

`AgentConfig` と `Goal` を作成し、`await AgentRunner(config).run(goal)` を呼びます。標準出力とMongoDBをフレームワークの設定から初期化する場合は、呼び出しを `with progress_logging(config):` で囲みます。アプリでSenda-Argusの初期化を管理している場合は、その設定で `AgentRunner` を利用できます。

## テスト

```bash
python -m pytest
SENDA_TEST_OLLAMA=1 python -m pytest tests/test_ollama.py
```

実機テストはOpenAI互換APIで `qwen3-coder:30b` を呼び、SQLiteからの取得、ファクトの記録、ゴール判定とHookの実行IDを確認します。
