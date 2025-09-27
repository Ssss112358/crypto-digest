from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple, Optional

from telethon import TelegramClient, types, functions
from telethon.sessions import StringSession

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def dtfmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')


def _peer_id(entity: Any) -> Optional[int]:
    if isinstance(entity, (types.Channel, types.Chat)):
        return entity.id
    return None


def _index_dialogs(client: TelegramClient) -> Dict[str, Any]:
    index = {
        'by_username': {},
        'by_id': {},
        'list': [],  # (title, username, id)
    }
    async def _collect():
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            title = getattr(entity, 'title', '') or getattr(entity, 'first_name', '') or ''
            username = (getattr(entity, 'username', None) or '').lower()
            cid = _peer_id(entity)
            index['list'].append((title, username, cid, entity))
            if username:
                index['by_username'][username] = entity
            if cid is not None:
                index['by_id'][cid] = entity
    return index, _collect


def _parse_spec_token(token: str) -> Tuple[str, str]:
    tok = token.strip()
    if tok.startswith('link:') or tok.startswith('http'):
        return 'link', tok.split(':', 1)[-1].strip()
    if tok.startswith('@'):
        return 'username', tok[1:].strip().lower()
    if tok.startswith('username:'):
        return 'username', tok.split(':', 1)[-1].strip().lower()
    if tok.startswith('title~='):
        return 'title_regex', tok.split('=', 1)[-1].strip()
    if tok.startswith('title:'):
        return 'title_exact', tok.split(':', 1)[-1].strip()
    if tok.startswith('id:'):
        return 'id', tok.split(':', 1)[-1].strip()
    return 'username', tok.lower()


def _try_parse_c_link(url: str) -> Optional[int]:
    match = re.search(r"t\.me/(?:c/)?(\d+)", url)
    if not match:
        return None
    return int(match.group(1))


async def _resolve_one(client: TelegramClient, index: Dict[str, Any], token: str) -> Tuple[Optional[Any], str]:
    kind, value = _parse_spec_token(token)

    if kind == 'username':
        entity = index['by_username'].get(value)
        if entity:
            return entity, f"username:{value}"
        try:
            entity = await client.get_entity(value)
            return entity, f"username:{value}(net)"
        except Exception as exc:
            return None, f"unresolved username:{value} ({exc})"

    if kind == 'title_exact':
        for title, username, cid, entity in index['list']:
            if title == value:
                return entity, f"title:{value}"
        return None, f"notfound title:{value}"

    if kind == 'title_regex':
        pattern = re.compile(value, re.IGNORECASE)
        for title, username, cid, entity in index['list']:
            if pattern.search(title or ''):
                return entity, f"title~={value} -> {title}"
        return None, f"notfound title~={value}"

    if kind == 'id':
        try:
            raw_id = int(value)
        except ValueError:
            return None, f"bad id:{value}"
        lookup = abs(raw_id)
        entity = index['by_id'].get(lookup)
        if entity:
            return entity, f"id:{value}"
        try:
            entity = await client.get_entity(raw_id)
            return entity, f"id:{value}(net)"
        except Exception as exc:
            return None, f"notfound id:{value} ({exc})"

    if kind == 'link':
        url = value
        if 'joinchat' in url or '/+' in url:
            try:
                invite_hash = url.rsplit('/', 1)[-1].lstrip('+')
                await client(functions.messages.ImportChatInviteRequest(hash=invite_hash))
            except Exception:
                pass
        cid = _try_parse_c_link(url)
        if cid is not None:
            entity = index['by_id'].get(cid)
            if entity:
                return entity, f"link:c/{cid}"
        match = re.search(r"t\.me/(@?)([A-Za-z0-9_]+)", url)
        if match:
            uname = match.group(2).lower()
            entity = index['by_username'].get(uname)
            if entity:
                return entity, f"link:@{uname}"
            try:
                entity = await client.get_entity(uname)
                return entity, f"link:@{uname}(net)"
            except Exception as exc:
                return None, f"unresolved link:{url} ({exc})"
        return None, f"bad link:{url}"

    return None, f"unknown spec:{token}"


async def resolve_sources(client: TelegramClient, specs: List[str]) -> Tuple[List[Any], List[str]]:
    index, collect = _index_dialogs(client)
    await collect()
    resolved = []
    notes = []
    for token in specs:
        entity, note = await _resolve_one(client, index, token)
        notes.append(note)
        if entity:
            resolved.append(entity)
    return resolved, notes


async def fetch_messages_smart(hours: int, source_specs: List[str],
                               string_session: str, api_id: int, api_hash: str
                              ) -> List[Dict[str, Any]]:
    cutoff = utcnow() - timedelta(hours=hours)
    rows: List[Dict[str, Any]] = []

    async with TelegramClient(StringSession(string_session), api_id, api_hash) as client:
        entities, notes = await resolve_sources(client, source_specs)
        print('[resolve]', '; '.join(notes))

        for entity in entities:
            username = getattr(entity, 'username', None) or ''
            title = getattr(entity, 'title', '') or getattr(entity, 'first_name', '') or ''
            count = 0
            async for message in client.iter_messages(entity, offset_date=cutoff, reverse=True):
                dt = message.date.replace(tzinfo=UTC)
                if dt < cutoff:
                    continue
                text = (message.message or '').strip()
                if not text:
                    continue
                link = f"https://t.me/{username}/{message.id}" if username else None
                rows.append({
                    'chat': title or username or str(entity.id),
                    'chat_title': title,
                    'chat_username': username,
                    'id': message.id,
                    'date': dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'from': (getattr(message.sender, 'username', None)
                             or getattr(message.sender, 'first_name', '')
                             or ''),
                    'text': text,
                    'link': link,
                })
                count += 1
            print(f"[info] {title or username or entity.id}: {count} msgs")

    return rows


async def fetch_messages(hours: int, sources: list[str], string_session: str, api_id: int, api_hash: str) -> List[Dict[str, Any]]:
    specs = []
    for token in sources:
        trimmed = token.strip()
        if not trimmed:
            continue
        if trimmed.startswith(('title:', 'title~=', 'link:', 'id:', '@', 'username:')):
            specs.append(trimmed)
        else:
            specs.append(f"username:{trimmed.lstrip('@')}")
    return await fetch_messages_smart(hours, specs, string_session, api_id, api_hash)
