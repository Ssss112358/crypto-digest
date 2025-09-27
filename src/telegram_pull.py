from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from telethon import TelegramClient
from telethon.sessions import StringSession


def utcnow():
    return datetime.now(timezone.utc)

def dtfmt(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def fetch_messages(hours: int, sources: list[str], string_session: str, api_id: int, api_hash: str) -> List[Dict[str, Any]]:
    cutoff = utcnow() - timedelta(hours=hours)
    collected: List[Dict[str, Any]] = []

    async with TelegramClient(StringSession(string_session), api_id, api_hash) as client:
        for raw_name in sources:
            uname = raw_name.lstrip('@')
            try:
                entity = await client.get_entity(uname)
            except Exception as exc:
                print(f"[warn] fetch_messages: unable to resolve source '{raw_name}': {exc}")
                continue

            username = getattr(entity, 'username', None)
            fetched = 0
            async for message in client.iter_messages(entity, offset_date=cutoff, reverse=True):
                text = (message.message or '').strip()
                if not text:
                    continue
                link = f"https://t.me/{username}/{message.id}" if username else None
                collected.append({
                    'chat': uname,
                    'chat_username': username or '',
                    'id': message.id,
                    'date': dtfmt(message.date.replace(tzinfo=timezone.utc)),
                    'from': (getattr(message.sender, 'username', None)
                             or getattr(message.sender, 'first_name', '')
                             or ''),
                    'text': text,
                    'link': link,
                })
                fetched += 1

            if fetched == 0:
                print(f"[info] fetch_messages: no messages for '{uname}' in the last {hours}h")

    return collected
