# -*- coding: utf-8 -*-
import os
import json
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone

from src.telegram_pull import fetch_messages
from src.bundler import bundle_conversations, bundles_to_text
from src.ai.analysis import analyze_digest
from src.delivery.discord import post_markdown

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"


def utcnow():
    return datetime.now(timezone.utc)

def dtfmt(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def build_evidence_map(msgs):
    """Map ISO timestamp to t.me link when available."""
    mapping = {}
    for msg in msgs:
        ts = msg.get('date')
        link = msg.get('link')
        if ts and ts not in mapping:
            mapping[ts] = link
    return mapping

def as_markdown(data: dict, nowstr: str, recent_hours: int, evidence_map: dict[str, str]) -> str:
    ov = data.get('overall_24h', {})
    dc = data.get('delta_recent', {})

    def tag_first_evidence(eids):
        if not isinstance(eids, list) or not eids:
            return ''
        ts = eids[0]
        hhmm = ts[11:16] if isinstance(ts, str) and len(ts) >= 16 else ''
        url = evidence_map.get(ts)
        if url and hhmm:
            return f' [{hhmm}]({url})'
        return f' {hhmm}' if hhmm else ''

    lines = [f"**AIまとめ（{nowstr} UTC）**", '']
    lines.append('**過去24h 全体要約**')
    if ov.get('summary'):
        lines.append(ov['summary'])
    if ov.get('top_entities'):
        lines.append('')
        lines.append('上位言及: ' + ', '.join(ov['top_entities']))
    if ov.get('events'):
        lines.append('')
        lines.append('重要イベント:')
        for event in ov['events'][:6]:
            suffix = tag_first_evidence(event.get('evidence_ids', []))
            lines.append(f"- {event.get('title','')} ({event.get('when','')}){suffix}")

    lines.append('')
    lines.append(f"**直近{dc.get('window_hours', recent_hours)}h の新規/変化点**")
    if dc.get('new_topics'):
        lines.append('_新規_')
        for topic in dc['new_topics'][:8]:
            suffix = tag_first_evidence(topic.get('evidence_ids', []))
            lines.append(f"- {topic.get('title','')}: {topic.get('what_changed','')}{suffix}")
    if dc.get('updates'):
        lines.append('_更新_')
        for topic in dc['updates'][:8]:
            suffix = tag_first_evidence(topic.get('evidence_ids', []))
            lines.append(f"- {topic.get('title','')}: {topic.get('what_changed','')}{suffix}")
    if dc.get('deadlines'):
        lines.append('_締切_')
        for item in dc['deadlines'][:6]:
            suffix = tag_first_evidence(item.get('evidence_ids', []))
            lines.append(f"- {item.get('item','')} → {item.get('due','')}{suffix}")
    return '\n'.join(lines)


def main():
    api_id = int(os.getenv('TG_API_ID', '0'))
    api_hash = os.getenv('TG_API_HASH', '')
    string_session = os.getenv('TG_STRING_SESSION', '')
    sources = [s.strip() for s in os.getenv('SOURCE_CHATS', '').split(',') if s.strip()]

    google_api_key = os.getenv('GOOGLE_API_KEY', '')
    gemini_model = os.getenv('GEMINI_MODEL', 'models/gemini-2.0-flash')
    discord_webhook = os.getenv('DISCORD_WEBHOOK_URL', '')

    hours_24 = int(os.getenv('HOURS_24', '24'))
    hours_recent = int(os.getenv('HOURS_RECENT', '6'))
    quiet = os.getenv('QUIET_LOG', '0') == '1'
    dry_run = os.getenv('DRY_RUN', '0') == '1'

    now = utcnow()

    if dry_run:
        dummy = {
            'overall_24h': {
                'summary': '(dry-run) 24h summary',
                'top_entities': ['HANA(12)', 'XPL(9)'],
                'events': []
            },
            'delta_recent': {
                'window_hours': hours_recent,
                'new_topics': [
                    {'title': 'A', 'what_changed': 'x', 'evidence_ids': []}
                ],
                'updates': [],
                'deadlines': []
            }
        }
        markdown = as_markdown(dummy, dtfmt(now), hours_recent, {})
        print('[dry-run] markdown length:', len(markdown))
        return

    msgs_24 = asyncio_run(fetch_messages(hours_24, sources, string_session, api_id, api_hash))
    counts = Counter(msg.get('chat') for msg in msgs_24)
    if not quiet:
        print(f'[info] telegram 24h counts: {dict(counts)}')

    if msgs_24:
        msgs_recent = [
            msg for msg in msgs_24
            if (now - datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)).total_seconds() <= hours_recent * 3600
        ]
        evidence_map = build_evidence_map(msgs_24)

        bundles_24 = bundle_conversations(msgs_24, window_min=8)
        bundles_recent = bundle_conversations(msgs_recent, window_min=8)
        text_24 = bundles_to_text(bundles_24)
        text_recent = bundles_to_text(bundles_recent)

        result = analyze_digest(google_api_key, text_24, text_recent, hours_recent, gemini_model)
    else:
        if not quiet:
            print(f'[warn] No Telegram messages found in the last {hours_24}h for sources: {sources}')
        msgs_recent = []
        evidence_map = {}
        result = {
            'overall_24h': {'summary': '該当データなし', 'top_entities': [], 'events': []},
            'delta_recent': {'window_hours': hours_recent, 'new_topics': [], 'updates': [], 'deadlines': []}
        }

    post_mode = os.getenv('DISCORD_POST_MODE', 'markdown')
    markdown = as_markdown(result, dtfmt(now), hours_recent, evidence_map)

    if post_mode == 'embed':
        import requests
        payload = {
            'embeds': [{
                'title': 'AI???',
                'description': markdown[:4000],
                'footer': {'text': 'crypto-digest - automated'}
            }]
        }
        response = requests.post(discord_webhook, json=payload, timeout=30)
        if response.status_code >= 300:
            print(f'[warn] discord status={response.status_code}')
    else:
        post_markdown(discord_webhook, markdown)

    if not quiet:
        print(f'[ok] posted digest. 24h={len(msgs_24)} recent={len(msgs_recent)}')

    state = {
        'last_run': dtfmt(now),
        'counts': {'24h': len(msgs_24), 'recent': len(msgs_recent)}
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

def asyncio_run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)

if __name__ == '__main__':
    import traceback
    import sys
    try:
        main()
    except Exception as exc:
        print('[fatal] run_digest_job failed:', type(exc).__name__, str(exc)[:300])
        traceback.print_exc()
        sys.exit(1)
