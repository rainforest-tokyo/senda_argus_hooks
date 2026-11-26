import argparse
import asyncio
import sys
from pathlib import Path

from senda_autonomous_agent import (
    AgentConfig, AgentLLMConfig, AgentRunner, Goal, LLMConfig, SystemPrompts, progress_logging,
)
from senda_autonomous_agent.config import MCPConfig, RAGConfig


def python_execution_example() -> tuple[AgentConfig, Goal]:
    config_path = Path("examples/python-ollama.json")
    config = AgentConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    goal = Goal(
        purpose="サンプルカフェの営業時間の集計",
        goal="SQLite RAGで「営業時間」を検索し、サンプルカフェの資料からPythonで平日の営業分数を計算する。",
        criteria=["Pythonの出力JSONに open_time、close_time、open_minutes があり、元資料から計算できる。"],
    )
    return config, goal


def mcp_rag_example() -> tuple[AgentConfig, Goal]:
    defaults = SystemPrompts()
    llm = LLMConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3-coder:30b",
        system_prompt="Use the supplied evidence for the café guidance task. Write explanations in Japanese.",
        system_prompts=SystemPrompts(
            strategy_planner=(
                f"{defaults.strategy_planner} Select SQLite RAG for weekday opening hours and the read-only "
                "MCP tool for the contact email."
            ),
            execution_planner=(
                f"{defaults.execution_planner} Search for 営業時間 in the SQLite RAG step and use the selected "
                "MCP tool for the email."
            ),
            goal_judge=(
                f"{defaults.goal_judge} Completion requires an opening-hours fact from SQLite RAG and a contact "
                "email fact from MCP. Include both evidence IDs in fact_ids."
            ),
        ),
        rag=RAGConfig(path=Path(".local/knowledge.sqlite")),
        mcp=[MCPConfig(
            name="sample-cafe",
            transport="stdio",
            command=sys.executable,
            args=[str(Path(__file__).resolve().parent / "examples" / "cafe_mcp_server.py")],
            allowed_tools=["get_contact"],
            help_resources={"get_contact": "help://sample-cafe/contact"},
        )],
    )
    config = AgentConfig(
        llm=AgentLLMConfig(default=llm),
        max_replans=2,
        hook_exporters=[{"type": "jsonl", "path": ".local/mcp-rag-hooks.jsonl"}],
    )
    goal = Goal(
        purpose="サンプルカフェの案内の確認",
        goal="平日の営業時間をSQLite RAGから、問い合わせ用メールアドレスをMCPから取得する。",
        criteria=[
            "SQLite RAGの資料から平日の開店・閉店時刻を確認できる。",
            "MCPが返したデータから問い合わせ用メールアドレスを確認できる。",
        ],
    )
    return config, goal


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a sample using the installed agent framework")
    parser.add_argument("--example", choices=("python", "mcp-rag"), default="python",
                        help="Choose Python execution or the programmatic MCP and RAG configuration")
    args = parser.parse_args()
    config, goal = mcp_rag_example() if args.example == "mcp-rag" else python_execution_example()

    try:
        with progress_logging(config):
            result = asyncio.run(AgentRunner(config).run(goal))
    except KeyboardInterrupt:
        return 130

    print(result.model_dump_json(indent=2))
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
