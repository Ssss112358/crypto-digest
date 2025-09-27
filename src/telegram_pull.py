from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from telethon import TelegramClient
from telethon.sessions import StringSession

def utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

def dtfmt(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

async def fetch_messages(hours: int, sources: list[str], string_session: str, api_id: int, api_hash: str) -> List[Dict[str, Any]]:
    cutoff = utcnow() - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    async with TelegramClient(StringSession(string_session), api_id, api_hash) as client:
        for uname in sources:
            uname = uname.lstrip('@')
            try:
                entity = await client.get_entity(uname)
            except Exception:
                continue
            username = getattr(entity, 'username', None)
            async for m in client.iter_messages(entity, offset_date=cutoff, reverse=True):
                text = (m.message or '').strip()
                if not text:
                    continue
                link = f"https://t.me/{username}/{m.id}" if username else None
                out.append({
                    'chat': uname,
                    'chat_username': username or '',
                    'id': m.id,
                    'date': dtfmt(m.date.replace(tzinfo=timezone.utc)),
                    'from': (getattr(m.sender, 'username', None) or getattr(m.sender, 'first_name', '') or ''),
                    'text': text,
                    'link': link,
                })
    return out
