from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError

from .config import AgentConfig
from .contracts import Goal
from .knowledge import import_documents, read_document
from .progress import progress_logging
from .runner import AgentRunner


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
    run.add_argument("--criterion", action="append", default=[])
    args = parser.parse_args()

    try:
        if args.command == "import":
            count = import_documents(args.db, (read_document(path) for path in _source_files(args.paths)))
            print(f"{count} 件の文書を登録しました")
            return 0
        settings = AgentConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
        goal = Goal(purpose=args.purpose, goal=args.goal, criteria=args.criterion)
        with progress_logging(settings):
            result = asyncio.run(AgentRunner(settings).run(goal))
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
