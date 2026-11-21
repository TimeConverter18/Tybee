from __future__ import annotations

import html
import mimetypes
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.db.base import session_factory
from app.db.models import Cover, SupportAttachment, SupportThread, SupportThreadMessage, User
from app.support.attachments import store_bytes

_MESSAGE_START = re.compile(r'<div class="message ([^"]+)" id="message([^"]+)">')
_DATE_RE = re.compile(r'class="pull_right date details" title="([^"]+)"')
_REPLY_RE = re.compile(r"GoToMessage\((\d+)\)")
_SENDER_RE = re.compile(r'<div class="from_name">(.*?)</div>', re.S)
_TEXT_RE = re.compile(r'<div class="text">(.*?)</div>', re.S)
_SERVICE_TEXT_RE = re.compile(r'<div class="body details">(.*?)</div>', re.S)
_MEDIA_RE = re.compile(r'(?:href|src)="((?:photos|video_files|voice_messages|files|stickers)/[^"]+)"')
_TOPIC_USER_RE = re.compile(r"·\s*(\d+)")
_ORDER_ID_RE = re.compile(r"#\s*(\d{1,19})")
_BUTTON_BLOCK_RE = re.compile(r'<div class="bot_button">(.*?)</div>', re.S)
_BUTTON_LINK_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)
_BUTTON_DATA_RE = re.compile(r"Data:\s*(.*?)\s*\|\s*Type:\s*callback")
_BOT_SYSTEM_PREFIXES = ("🧵", "📝", "📦", "✅", "❌", "🔔", "💬", "🤝", "🚫")
_NULL = chr(92) + "N"
_COPY_END = chr(92) + "."


@dataclass(frozen=True)
class ExportedMessage:
    id: int
    user_id: int
    body: str
    direction: str
    created_at: datetime
    media: tuple[Path, ...]
    order_id: int | None = None
    buttons: tuple[str, ...] = ()

    @property
    def request_key(self) -> str:
        return f"telegram-export:{self.id}"


def _plain(value: str) -> str:
    def link_text(match: re.Match) -> str:
        href = html.unescape(match.group(1)).strip()
        label = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        if not href or href.startswith(("tel:", "javascript:")) or href == label:
            return label
        if label.startswith("@") and href.startswith("https://t.me/"):
            return label
        return f"{label} ({href})"

    value = re.sub(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', link_text, value, flags=re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _pg_text(value: str | None) -> str:
    if value is None or value == _NULL:
        return ""
    return (
        value.replace("\\t", "\t")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\\\", "\\")
    )


def _last_copy_rows(path: Path, table: str) -> list[dict[str, str]]:
    marker = f"COPY public.{table} ("
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    in_copy = False
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not in_copy and line.startswith(marker):
                columns = line.removeprefix("COPY public.").split("(", 1)[1].split(") FROM stdin;", 1)[0].split(", ")
                rows = []
                in_copy = True
            elif in_copy and line.rstrip("\r\n") == _COPY_END:
                in_copy = False
            elif in_copy:
                rows.append(dict(zip(columns, line.rstrip("\r\n").split("\t"))))
    return rows


def _old_users(dump_path: Path) -> dict[int, dict[str, str]]:
    return {int(row["id"]): row for row in _last_copy_rows(dump_path, "users")}


def _relay_users(dump_path: Path) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in _last_copy_rows(dump_path, "relay_map"):
        message_id = int(row["support_message_id"])
        user_id = int(row["user_id"])
        if message_id in result and result[message_id] != user_id:
            raise ValueError(f"Conflicting relay mapping for message {message_id}")
        result[message_id] = user_id
    return result


def _media_paths(block: str, root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for raw in _MEDIA_RE.findall(block):
        if raw.endswith("_thumb.jpg") or raw.endswith("_thumb.webp"):
            continue
        path = (root / html.unescape(raw)).resolve()
        if root.resolve() not in path.parents:
            raise ValueError("Media path escapes the export directory")
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def _buttons(block: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw in _BUTTON_BLOCK_RE.findall(block):
        decoded = html.unescape(raw)
        link = _BUTTON_LINK_RE.search(decoded)
        data = _BUTTON_DATA_RE.search(decoded)
        label = _plain(link.group(1)) if link else "Кнопка"
        result.append(f"{label} — {data.group(1)}" if data else label)
    return tuple(result)


def _with_buttons(body: str, buttons: tuple[str, ...]) -> str:
    if not buttons:
        return body
    suffix = "Кнопки:\n" + "\n".join(f"• {button}" for button in buttons)
    return f"{body}\n\n{suffix}" if body else suffix


def _topic_report(history: list[ExportedMessage], old_users: dict[int, dict[str, str]]) -> list[dict]:
    by_user: dict[int, dict] = {}
    covered = {message.user_id for message in history}
    for user_id, old in old_users.items():
        if old.get("support_thread_id") in (None, _NULL) and user_id not in covered:
            continue
        by_user[user_id] = {
            "user_id": user_id,
            "messages": 0,
            "buttons": 0,
            "media": 0,
            "orders": 0,
            "first_at": None,
            "last_at": None,
        }
    for message in history:
        row = by_user[message.user_id]
        row["messages"] += 1
        row["buttons"] += len(message.buttons)
        row["media"] += len(message.media)
        row["orders"] += message.order_id is not None
        stamp = message.created_at.isoformat()
        if row["first_at"] is None or stamp < row["first_at"]:
            row["first_at"] = stamp
        if row["last_at"] is None or stamp > row["last_at"]:
            row["last_at"] = stamp
    return sorted(by_user.values(), key=lambda row: row["user_id"])


def load_history(export_dir: Path, dump_path: Path) -> tuple[list[ExportedMessage], dict]:
    relay_users = _relay_users(dump_path)
    old_users = _old_users(dump_path)
    raw_messages: dict[int, dict] = {}
    topic_users: dict[int, int] = {}
    bot_names: set[str] = set()

    text = (export_dir / "messages.html").read_text(encoding="utf-8")
    for block in re.split(r"(?=<div class=\"message )", text):
        match = _MESSAGE_START.match(block)
        if not match:
            continue
        classes, raw_id = match.groups()
        try:
            message_id = int(raw_id)
        except ValueError:
            continue
        service = "service" in classes.split()
        reply = _REPLY_RE.search(block)
        sender_match = _SENDER_RE.search(block)
        sender = _plain(sender_match.group(1)) if sender_match else ""
        text_match = _SERVICE_TEXT_RE.search(block) if service else _TEXT_RE.search(block)
        body = _plain(text_match.group(1)) if text_match else ""
        if service and "created topic" in body:
            actor = body.split(" created topic", 1)[0].strip()
            if actor:
                bot_names.add(actor)
            user_match = _TOPIC_USER_RE.search(body)
            if user_match:
                topic_users[message_id] = int(user_match.group(1))
        date_match = _DATE_RE.search(block)
        raw_messages[message_id] = {
            "service": service,
            "reply_to": int(reply.group(1)) if reply else None,
            "sender": sender,
            "body": body,
            "created_at": datetime.strptime(html.unescape(date_match.group(1)), "%d.%m.%Y %H:%M:%S UTC%z") if date_match else None,
            "media": _media_paths(block, export_dir) if not service else (),
            "buttons": _buttons(block) if not service else (),
        }

    order_users = {
        int(row["id"]): int(row["user_id"])
        for row in _last_copy_rows(dump_path, "orders")
    }
    mapped: dict[int, int] = {}
    for message_id, message in raw_messages.items():
        seen: set[int] = set()
        target = message_id if message_id in topic_users else message["reply_to"]
        while target is not None and target not in seen:
            seen.add(target)
            if target in topic_users:
                mapped[message_id] = topic_users[target]
                break
            if target in relay_users:
                mapped[message_id] = relay_users[target]
                break
            target = raw_messages.get(target, {}).get("reply_to")
        if message_id not in mapped:
            order_match = _ORDER_ID_RE.search(message["body"])
            order_user = order_users.get(int(order_match.group(1))) if order_match else None
            if order_user is not None:
                mapped[message_id] = order_user

    history: list[ExportedMessage] = []
    previous_sender = ""
    skipped = Counter()
    for message_id, message in sorted(raw_messages.items()):
        sender = message["sender"] or previous_sender
        if message["sender"]:
            previous_sender = sender
        if message["service"]:
            continue
        user_id = mapped.get(message_id)
        if user_id is None:
            skipped["unmapped"] += 1
            continue
        if user_id not in old_users:
            skipped["missing_user"] += 1
            continue
        if message["created_at"] is None:
            skipped["missing_date"] += 1
            continue
        buttons = message["buttons"]
        body = _with_buttons(message["body"], buttons)
        media = message["media"]
        if not body and not media:
            skipped["empty"] += 1
            continue
        if sender in bot_names:
            is_bot_service = body.startswith(_BOT_SYSTEM_PREFIXES) or message_id not in relay_users
            direction = "system" if is_bot_service else "client"
        else:
            direction = "staff"
        order_match = _ORDER_ID_RE.search(body)
        order_id = int(order_match.group(1)) if order_match else None
        history.append(
            ExportedMessage(
                message_id,
                user_id,
                body,
                direction,
                message["created_at"],
                media,
                order_id=order_id,
                buttons=buttons,
            )
        )

    stats = {
        "messages": len(history),
        "clients": len({message.user_id for message in history}),
        "directions": dict(Counter(message.direction for message in history)),
        "media": sum(len(message.media) for message in history),
        "orders": sum(message.order_id is not None for message in history),
        "buttons": sum(len(message.buttons) for message in history),
        "media_missing": sum(not path.is_file() for message in history for path in message.media),
        "topics": _topic_report(history, old_users),
        "skipped": dict(skipped),
    }
    return history, stats


async def import_history(
    export_dir: Path,
    dump_path: Path,
    *,
    execute: bool = False,
    factory=None,
    user_id: int | None = None,
) -> dict:
    history, stats = load_history(export_dir, dump_path)
    if user_id is not None:
        history = [message for message in history if message.user_id == user_id]
        stats["messages"] = len(history)
        stats["clients"] = len({message.user_id for message in history})
        stats["directions"] = dict(Counter(message.direction for message in history))
        stats["media"] = sum(len(message.media) for message in history)
        stats["orders"] = sum(message.order_id is not None for message in history)
        stats["buttons"] = sum(len(message.buttons) for message in history)
        stats["topics"] = _topic_report(history, _old_users(dump_path))
        stats["media_missing"] = sum(not path.is_file() for message in history for path in message.media)
    if not execute:
        return stats

    factory = factory or session_factory
    old_users = _old_users(dump_path)
    by_user: dict[int, list[ExportedMessage]] = {}
    for message in history:
        by_user.setdefault(message.user_id, []).append(message)

    imported = attachments = 0
    async with factory() as session:
        default_cover = await session.scalar(select(Cover).where(Cover.slug == "default"))
        existing_messages = {
            row.request_key: row
            for row in await session.scalars(
                select(SupportThreadMessage).where(SupportThreadMessage.request_key.is_not(None))
            )
        }
        for client_id, messages in by_user.items():
            old = old_users[client_id]
            user = await session.get(User, client_id)
            if user is None:
                user = User(
                    id=client_id,
                    username=_pg_text(old.get("username")) or None,
                    full_name=_pg_text(old.get("full_name")) or str(client_id),
                    is_banned=old.get("is_banned") == "t",
                    source="telegram",
                )
                session.add(user)
                await session.flush()
            thread = await session.scalar(select(SupportThread).where(SupportThread.user_id == client_id))
            created_thread = thread is None
            if thread is None:
                thread = SupportThread(
                    user_id=client_id,
                    channel="telegram",
                    status="closed",
                    last_cover_id=default_cover.id if default_cover else None,
                    unread=0,
                    needs_reply=False,
                    created_at=messages[0].created_at,
                    last_message_at=messages[-1].created_at,
                )
                session.add(thread)
                await session.flush()
            for message in messages:
                existing = existing_messages.get(message.request_key)
                if existing is not None:
                    if existing.order_id is None and message.order_id is not None:
                        existing.order_id = message.order_id
                    if existing.body != message.body:
                        existing.body = message.body
                    continue
                row = SupportThreadMessage(
                    thread_id=thread.id,
                    direction=message.direction,
                    kind="message",
                    body=message.body,
                    cover_id=thread.last_cover_id,
                    order_id=message.order_id,
                    request_key=message.request_key,
                    created_at=message.created_at,
                )
                session.add(row)
                await session.flush()
                existing_messages[message.request_key] = row
                imported += 1
                for path in message.media:
                    data = path.read_bytes()
                    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    stored = store_bytes(data, kind="image" if mime in ("image/jpeg", "image/png", "image/webp") else "document", mime=mime, original_name=path.name)
                    session.add(
                        SupportAttachment(
                            thread_message_id=row.id,
                            direction="in" if message.direction == "client" else "out",
                            ephemeral=False,
                            kind=stored.kind,
                            mime=stored.mime,
                            original_name=stored.original_name,
                            size=stored.size,
                            sha256=stored.sha256,
                            path=stored.path,
                        )
                    )
                    attachments += 1
            if created_thread:
                client_dates = [message.created_at for message in messages if message.direction == "client"]
                thread.last_client_message_at = max(client_dates) if client_dates else None
            await session.commit()
    stats = dict(stats)
    stats.update({"imported": imported, "attachments": attachments})
    return stats

