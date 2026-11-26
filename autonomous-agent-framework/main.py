import asyncio
from pathlib import Path

from senda_autonomous_agent import AgentConfig, AgentRunner, Goal, progress_logging


def main() -> int:
    config_path = Path("examples/python-ollama.json")
    config = AgentConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    goal = Goal(
        purpose="サンプルカフェの営業時間の集計",
        goal="SQLite RAGで「営業時間」を検索し、サンプルカフェの資料からPythonで平日の営業分数を計算する。",
        criteria=["Pythonの出力JSONに open_time、close_time、open_minutes があり、元資料から計算できる。"],
    )

    try:
        with progress_logging(config):
            result = asyncio.run(AgentRunner(config).run(goal))
    except KeyboardInterrupt:
        return 130

    print(result.model_dump_json(indent=2))
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
