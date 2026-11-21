from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from app.config import get_settings


@dataclass(frozen=True)
class RecoveryRef:
    support_message_id: int
    user_id: int
    topic_id: int


def _last_copy_rows(path: Path, table: str) -> list[dict[str, str]]:
    marker = f"COPY public.{table} ("
    end = chr(92) + "."
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    in_copy = False
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not in_copy and line.startswith(marker):
                columns = line.removeprefix("COPY public.").split("(", 1)[1].split(") FROM stdin;", 1)[0].split(", ")
                rows = []
                in_copy = True
            elif in_copy and line.rstrip("\r\n") == end:
                in_copy = False
            elif in_copy:
                rows.append(dict(zip(columns, line.rstrip("\r\n").split("\t"))))
    return rows


def load_recovery_refs(path: Path) -> list[RecoveryRef]:
    users = _last_copy_rows(path, "users")
    topics = {
        int(row["id"]): int(row["support_thread_id"])
        for row in users
        if row.get("support_thread_id") not in (None, chr(92) + "N")
    }
    refs: list[RecoveryRef] = []
    seen: set[int] = set()
    for row in _last_copy_rows(path, "relay_map"):
        message_id = int(row["support_message_id"])
        user_id = int(row["user_id"])
        if message_id in seen:
            raise ValueError(f"Duplicate relay message ID: {message_id}")
        if user_id not in topics:
            raise ValueError(f"Relay message {message_id} has no client topic")
        seen.add(message_id)
        refs.append(RecoveryRef(message_id, user_id, topics[user_id]))
    return sorted(refs, key=lambda ref: ref.support_message_id)


def _cover_token(slug: str) -> str:
    path = Path(get_settings().covers_root) / slug / "config.yaml"
    with path.open(encoding="utf-8") as source:
        token = str(yaml.safe_load(source).get("token") or "").strip()
    if not token:
        raise ValueError(f"Cover {slug} has no bot token")
    return token


def _completed_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    completed: set[int] = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                completed.add(int(json.loads(line)["support_message_id"]))
    return completed


async def export(args: argparse.Namespace) -> None:
    refs = load_recovery_refs(args.dump)
    if args.limit is not None:
        refs = refs[: args.limit]
    print(f"Recovery plan: {len(refs)} mapped messages; no database changes")
    if not args.execute:
        print("Dry run. Add --execute to forward messages into the recovery chat.")
        return

    completed = _completed_ids(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bot = Bot(_cover_token(args.cover))
    exported = errors = 0
    try:
        await bot.get_chat(args.source_chat)
        await bot.get_chat(args.target_chat)
        with args.output.open("a", encoding="utf-8") as output:
            for ref in refs:
                if ref.support_message_id in completed:
                    continue
                while True:
                    try:
                        message = await bot.forward_message(
                            chat_id=args.target_chat,
                            from_chat_id=args.source_chat,
                            message_id=ref.support_message_id,
                        )
                        record = {
                            "support_message_id": ref.support_message_id,
                            "user_id": ref.user_id,
                            "topic_id": ref.topic_id,
                            "message": message.model_dump(mode="json", exclude_none=True),
                        }
                        output.write(json.dumps(record, ensure_ascii=False) + "\n")
                        output.flush()
                        os.chmod(args.output, 0o600)
                        exported += 1
                        print(f"Exported {ref.support_message_id}")
                        break
                    except TelegramRetryAfter as exc:
                        time.sleep(float(exc.retry_after) + 0.1)
                    except TelegramAPIError as exc:
                        errors += 1
                        print(f"Skipped {ref.support_message_id}: {exc}")
                        break
                time.sleep(args.delay)
    finally:
        await bot.session.close()
    print(f"Finished: exported={exported}, skipped={errors}, output={args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export old mapped Telegram support messages without touching the application database."
    )
    parser.add_argument("dump", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-chat", type=int, help="Old forum supergroup ID")
    parser.add_argument("--target-chat", type=int, help="Separate recovery group ID")
    parser.add_argument("--cover", default="default", help="Existing cover whose bot is in both groups")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=3.1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute and (args.source_chat is None or args.target_chat is None):
        parser.error("--source-chat and --target-chat are required with --execute")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    asyncio.run(export(args))


if __name__ == "__main__":
    main()
