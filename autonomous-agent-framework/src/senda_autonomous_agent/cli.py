from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from langchain_core.messages import BaseMessage
from pydantic import ValidationError
from senda_argus_hooks.core.context import get_run_id, get_turn_id

from .config import AgentConfig, Role
from .contracts import Goal, PlanningStrategy
from .knowledge import import_documents, read_document
from .progress import progress_logging
from .runner import AgentRunner


_ROLE_LABELS = {"planner": "実行プランナー", "worker": "ワーカー", "goal_judge": "ゴール判定"}
_MESSAGE_LABELS = {"system": "システムプロンプト", "human": "ユーザー入力"}


def _source_files(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in {".yaml", ".yml", ".md", ".txt"}:
                    yield child
        else:
            if path.suffix.lower() not in {".yaml", ".yml", ".md", ".txt"}:
                raise ValueError(f"Unsupported import format: {path}")
            yield path


def _format_content(content: str | list[str | dict]) -> str:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return content
    return json.dumps(content, ensure_ascii=False, indent=2, default=str)


def _exchange_header(direction: str, role: Role, schema: str) -> str:
    role_label = "方針プランナー" if schema == PlanningStrategy.__name__ else _ROLE_LABELS[role]
    return (
        f"\n{'─' * 60}\n"
        f"LLM {direction}  {role_label} ({schema})\n"
        f"run_id   {get_run_id()}\n"
        f"turn_id  {get_turn_id()}"
    )


def _show_prompt(role: Role, schema: str, messages: Sequence[BaseMessage]) -> None:
    lines = [_exchange_header("送信", role, schema)]
    for message in messages:
        label = _MESSAGE_LABELS.get(message.type, message.type)
        content = message.content
        if message.type == "system" and isinstance(content, str):
            instructions, _, schema_line = content.rpartition("\n")
            if schema_line.startswith("{"):
                lines.extend((f"\n【{label}】", instructions, "\n【出力JSONスキーマ】", _format_content(schema_line)))
                continue
        lines.extend((f"\n【{label}】", _format_content(content)))
    print("\n".join(lines), end="\n\n", file=sys.stderr, flush=True)


def _show_response(role: Role, schema: str, response: BaseMessage) -> None:
    print(
        _exchange_header("受信", role, schema),
        "\n【レスポンス本文】",
        _format_content(response.content),
        sep="\n",
        end="\n\n",
        file=sys.stderr,
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Senda Autonomous Agent Framework")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("import", help="Import reference documents into SQLite")
    ingest.add_argument("--db", required=True, type=Path)
    ingest.add_argument("paths", nargs="+", type=Path)
    run = commands.add_parser("run", help="Run an agent to evaluate a goal")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--purpose", required=True)
    run.add_argument("--goal", required=True)
    run.add_argument("--criterion", action="append", default=[],
                     help="Optional completion condition for the goal judge. Repeat for multiple conditions")
    run.add_argument("--show-prompts", action="store_true",
                     help="Display each LLM request's system and user messages on stderr")
    run.add_argument("--show-responses", action="store_true",
                     help="Display each LLM response's content before JSON validation on stderr")
    args = parser.parse_args()

    try:
        if args.command == "import":
            count = import_documents(args.db, (read_document(path) for path in _source_files(args.paths)))
            print(f"{count} 件の文書を登録しました")
            return 0
        settings = AgentConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
        goal = Goal(purpose=args.purpose, goal=args.goal, criteria=args.criterion)
        with progress_logging(settings):
            runner = AgentRunner(
                settings,
                on_prompt=_show_prompt if args.show_prompts else None,
                on_response=_show_response if args.show_responses else None,
            )
            result = asyncio.run(runner.run(goal))
        print(result.model_dump_json(indent=2))
        return 0 if result.status == "completed" else 2
    except KeyboardInterrupt:
        return 130
    except ValidationError as error:
        for item in error.errors(include_input=False):
            field = ".".join(str(segment) for segment in item["loc"])
            print(f"入力エラー {field} ({item['type']})", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"実行エラー ({type(error).__name__}): {error}" if isinstance(error, ValueError)
              else f"実行エラー ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
