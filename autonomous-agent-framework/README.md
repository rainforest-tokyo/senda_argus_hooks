# Senda Autonomous Agent Framework

LLMが目的とゴールをもとに作業手順を立て、MCPやSQLiteから情報を集め、蓄積した根拠でゴールを判定するPythonフレームワークです。LLMとのやり取りにはLangChain、各周回の実行にはLangGraph、進捗と監査イベントにはSenda-Argus Hooksを使います。

- 方針プランナーはゴール、取得済みファクト、利用可能なツールの一覧から、調査の方針・必要な根拠・候補ツールをJSONで返します。
- 実行プランナーは選定済みツールの引数スキーマとHelpを読み、ワーカーへ渡す実行順、引数、期待するデータをJSONで返します。
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

`--criterion` はゴール判定に渡す任意の完了条件です。複数の条件を指定する場合は、条件ごとに `--criterion` を繰り返します。

各LLMリクエストのシステムメッセージは、英文の役割定義、目的別の `system_prompt`、機能別の `system_prompts`、共通の指示と出力JSONスキーマの順に組み立てます。`role_prompts` は役割定義、`system_prompts` は各機能が行う処理の指示です。JSONでは `llm.default.system_prompts` に指定します。

Pythonからは `LLMConfig` の初期化引数で指定できます。

```python
from senda_autonomous_agent import LLMConfig, SystemPrompts

llm_config = LLMConfig(
    model="qwen3-coder:30b",
    base_url="http://127.0.0.1:11434/v1",
    system_prompts=SystemPrompts(
        strategy_planner="Identify the evidence needed and select relevant tools.",
        execution_planner="Build ordered steps using the selected tools.",
        worker="Review each result and choose the next action.",
        goal_judge="Check the completion criteria and cite supporting fact IDs.",
    ),
)
```

複数の機能で同じ指示を使う場合は、`SystemPrompts` の `default` に一度だけ記述します。例えば `SystemPrompts(default="Base your decisions on the supplied evidence.", worker="Review each result and choose the next action.")` では、ワーカーに個別の指示を使い、残りの機能に共通の指示を使います。`default` も省略した項目には機能ごとの既定の英文を使います。JSONでも同じ `default` キーを指定できます。

個別の完了条件を確認する指示は `check_criterion` で指定できます。この項目にも `default` が適用されます。実行プランナーのユーザー入力に含まれる `operation` は、初回計画・修正・組み直しの種別を表します。

`senda-agent run` の `--show-prompts` はLLMへのシステムプロンプト・出力JSONスキーマ・入力本文を、`--show-responses` はLLMから受け取った本文をJSON検証前に標準エラー出力へ表示します。送信と受信は見出しで区切り、JSONはインデント付きで表示します。両方を指定すると、役割、`run_id`、`turn_id` で送受信を照合できます。表示にはRAGやMCPから取得した本文が含まれる場合があります。

## 情報源とファクト

`llm.default.rag` にSQLiteの `path` と一回の最大取得件数 `limit` を指定します。取り込みはYAML、プレーンテキスト、Markdownに対応しています。YAMLは `id` と `info` 内の `name`、`description`、`severity`、`tags` を取り込み、元ファイルのパスを出典にします。SQLiteの `documents` テーブルには `id`、`title`、`body`、`source`、`metadata` が入ります。

プランナーに渡す検索定義には、SQLiteに存在するタグや重要度と比較方法が含まれます。検索語はそれぞれタイトルまたは本文への部分一致で照合し、メタデータ条件を合わせられます。ファクトは `facts_dir` 内の実行IDごとのJSONLへ追記され、取得時刻、周回、出典、取得内容を記録します。

`llm.default.mcp` は情報収集用のMCPサーバー一覧です。各要素に `name`、`transport`、`allowed_tools` を設定します。`stdio` には `command` と必要に応じて `args`、`streamable_http` には `url` を指定します。必要に応じて `api_key_ref` を設定できます。プランナーへの公開対象は許可リストにあり、サーバーが `readOnlyHint=true` として公開するツールです。

### ツール選定とHelp

`ToolCatalog` がMCPとSQLite RAGのツールを登録し、方針プランナーへIDと用途を渡します。ツールIDはSQLite RAGなら `rag:retrieve_from_rag`、MCPなら例えば `mcp:office/get_hours` です。方針プランナーはモデルの共通システムプロンプトを使い、実行プランナーには `llm.overrides.planner.system_prompt` で個別の指示を設定できます。

選定後に取得するHelpは、次の場所に登録できます。

- **MCP** はサーバー設定の `help_resources` に、ツール名と公開されているリソースURIの対応を指定します。例えば `"help_resources": {"get_hours": "help://office/get_hours"}` です。実行時の引数はMCPの `inputSchema` に従って検証します。
- **SQLite RAG** は取り込むYAMLの `info.metadata` に `doc_type: tool_help` と `tool_id` を付けます。`info.description` の本文が、対応するツールのHelpになります。`examples/replan/documents/rag-help.yaml` に設定例があります。ワーカーが取得する根拠資料は `documents` 内の通常文書です。

`revise_plan` は既定で取得済みの方針とツールを使って実行計画を作り直します。`revise_plan` に `scope: strategy` を指定するか `rebuild_plan` を選ぶと、方針とツール候補から検討し直します。進捗ログでは方針・Help取得・実行計画の各段階を同じ `run_id` で追えます。

## 収集したファクトをPythonで加工

設定の最上位に `"python_execution": {}` を追加すると、方針プランナーのツール一覧に `local:transform_with_python` が加わります。実行プランナーは収集ステップの後に、加工内容と期待する出力を指定します。`required_output_keys` を指定すると、出力JSONの必須キーを検証します。`input_fact_ids` を省略すると、同じ周回の先行ステップで取得したものも含め、その時点のファクトを使います。

ワーカーが生成したPythonをDockerの使い捨てコンテナで実行します。コンテナのネットワークは隔離し、入力はフレームワークが渡した最大256 KiBのファクトに限定します。スクリプトは一時領域の `input.json` を読み、標準出力へJSONオブジェクトを1件返します。`python_execution` の `image`、`artifacts_dir`、`timeout_seconds`、`max_output_bytes` でイメージ、成果物の保存先、実行時間、出力量を設定できます。既定値はそれぞれ `python:3.12-slim`、`.local/python-runs`、15秒、標準出力と標準エラーそれぞれ64 KiBです。DockerはローカルのUnixソケットに接続します。ソケットが標準の場所と異なる場合は `docker_socket` にパスを指定します。イメージには信頼できるローカルのPythonイメージを指定してください。コード生成用のシステムプロンプトは `llm.default.system_prompts.generate_python` で設定できます。

ローカルOllamaとDockerを起動した状態で、営業時間を取得して営業分数を計算できます。

```bash
docker pull python:3.12-slim
senda-agent import --db .local/knowledge.sqlite examples/documents
senda-agent run --show-prompts --show-responses --config examples/python-ollama.json \
  --purpose 'サンプルカフェの営業時間の集計' \
  --goal 'SQLite RAGでサンプルカフェの営業時間を取得し、Pythonで平日の営業分数を計算する。' \
  --criterion 'Pythonの出力JSONに open_time、close_time、open_minutes があり、元資料から計算できる。'
```

試行ごとの `script.py`、`input.json`、`stdout.log`、`stderr.log`、検証に通った `result.json` は `.local/python-runs/<run_id>/<artifact_id>/` に保存します。成功した結果には入力ファクトIDを付け、ゴール判定に渡します。実行や検証の失敗は試行履歴から再計画に利用できます。Senda-Argusの実行イベントは `run_id` と `artifact_id` で成果物と照合できます。

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

Senda-Argusへ送信するJSON本文を確認する場合は、`hook_exporters` の `type: argus` の項目に `"log_payload": true` を指定します。HTTPリクエストの直前に、送信する本文を `[senda-argus-http] payload=` に続けて標準エラー出力へ一行で表示します。HTTPステータスも確認するには `"log_http": true` を併用します。標準出力とまとめて保存する場合は、実行コマンドの末尾に `> agent.log 2>&1` を付けます。

## Pythonから実行

`AgentConfig` と `Goal` を作成し、`await AgentRunner(config).run(goal)` を呼びます。標準出力とMongoDBをフレームワークの設定から初期化する場合は、呼び出しを `with progress_logging(config):` で囲みます。アプリでSenda-Argusの初期化を管理している場合は、その設定で `AgentRunner` を利用できます。

### wheelをインストールしてmain.pyを実行

このディレクトリで、Frameworkと依存するSenda-Argus Hooksのwheelをインストールします。`main.py` はパッケージの公開APIを使って、サンプルカフェの営業分数を計算します。

```bash
python3 -m venv .local/wheel-env
source .local/wheel-env/bin/activate
python -m pip install dist/senda_argus_hooks-0.8.0-py3-none-any.whl \
  dist/senda_autonomous_agent-0.1.0-py3-none-any.whl

docker pull python:3.12-slim
senda-agent import --db .local/knowledge.sqlite examples/documents
python main.py
```

実行時はDockerと、`qwen3-coder:30b` を登録したローカルOllamaを起動してください。接続先や保存先は `examples/python-ollama.json`、目的・ゴール・完了条件は `main.py` で変更できます。ソース配布用のアーカイブにも `main.py` と `examples/` が含まれます。

ソースから配布ファイルを作る場合は、ビルド用の環境で以下を実行します。

```bash
python -m pip install build
python -m build --wheel --outdir "$PWD/dist" ../python
python -m build
```

## テスト

```bash
python -m pytest
SENDA_TEST_OLLAMA=1 python -m pytest tests/test_ollama.py
SENDA_TEST_OLLAMA=1 SENDA_TEST_DOCKER=1 python -m pytest
```

実機テストはOpenAI互換APIで `qwen3-coder:30b` を呼び、SQLiteからの取得、ファクトの記録、ゴール判定とHookの実行IDを確認します。

### Ollamaで3周回の再計画を確認

```bash
SENDA_TEST_OLLAMA=1 python -m pytest -q -s tests/test_ollama.py -k replans_across_rounds
```

再現のため、サンプル設定は初回に存在しない旧資料名を検索します。1周目は0件でワーカーから再計画します。2周目は営業時間と照会コードを取得しますが、連絡先の根拠がまだ足りないためゴール判定から再計画します。3周目は先に得た照会コードで連絡先資料を取得し、ゴール判定が2件のファクトを根拠に完了を返すことを確認します。

同じシナリオをCLIから実行すると、各役割のプロンプトとレスポンスも確認できます。1周回の中で各役割がLLMを呼ぶため、このシナリオでは3周回の間に複数の送受信が表示されます。

```bash
senda-agent import --db .local/replan-knowledge.sqlite examples/replan/documents
senda-agent run --config examples/replan/ollama.json --show-prompts --show-responses \
  --purpose '窓口案内の確認' \
  --goal '平日の窓口の営業時間と、問い合わせ用メールアドレスを資料から確認する。' \
  --criterion '平日の開始・終了時刻を根拠付きで確認できる。' \
  --criterion '問い合わせ用メールアドレスを根拠付きで確認できる。'
```
