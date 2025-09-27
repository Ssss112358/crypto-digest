# -*- coding: utf-8 -*-
import os, json
from pathlib import Path
from datetime import datetime, timezone

from src.telegram_pull import fetch_messages
from src.bundler import bundle_conversations, bundles_to_text
from src.ai.analysis import analyze_digest
from src.delivery.discord import post_markdown

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"; STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"

def utcnow():
    return datetime.now(timezone.utc)

def dtfmt(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def as_markdown(data: dict, nowstr: str, recent_hours: int) -> str:
    ov = data.get("overall_24h", {})
    dc = data.get("delta_recent", {})
    lines = [f"**AIまとめ（{nowstr} UTC）**", ""]
    lines.append("**過去24h 全体要約**")
    if ov.get("summary"): lines.append(ov["summary"])
    if ov.get("top_entities"): lines.append("\n上位言及: " + ", ".join(ov["top_entities"]))
    if ov.get("events"):
        lines.append("\n重要イベント:")
        for e in ov["events"][:6]:
            lines.append(f"- {e.get('title','')} believable ({e.get('when','')})")

    lines.append("")
    lines.append(f"**直近{dc.get('window_hours', recent_hours)}h の新規/変化点**")
    if dc.get("new_topics"):
        lines.append("_新規_")
        for t in dc["new_topics"][:8]:
            lines.append(f"- {t.get('title','')}: {t.get('what_changed','')}")
    if dc.get("updates"):
        lines.append("_更新_")
        for t in dc["updates"][:8]:
            lines.append(f"- {t.get('title','')}: {t.get('what_changed','')}")
    if dc.get("deadlines"):
        lines.append("_締切_")
        for d in dc["deadlines"][:6]:
            lines.append(f"- {d.get('item','')} → {d.get('due','')}")
    return "\n".join(lines)

def main():
    # env
    API_ID = int(os.getenv("TG_API_ID", "0"))
    API_HASH = os.getenv("TG_API_HASH", "")
    STRING_SESSION = os.getenv("TG_STRING_SESSION", "")
    SOURCES = [s.strip().lstrip("@") for s in os.getenv("SOURCE_CHATS","").split(",") if s.strip()]

    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash")

    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

    HOURS_24 = int(os.getenv("HOURS_24", "24"))
    HOURS_RECENT = int(os.getenv("HOURS_RECENT", "6"))
    QUIET = os.getenv("QUIET_LOG","0") == "1"

    # 収集（24hまとめてpull → recentはフィルタ）
    msgs_24 = asyncio_run(fetch_messages(HOURS_24, SOURCES, STRING_SESSION, API_ID, API_HASH))
    now = utcnow()
    msgs_recent = [m for m in msgs_24 if (now - datetime.strptime(m["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() <= HOURS_RECENT*3600]

    # バンドル → LLMに渡すテキスト化
    b24 = bundle_conversations(msgs_24, window_min=8)
    brc = bundle_conversations(msgs_recent, window_min=8)
    text_24 = bundles_to_text(b24)
    text_rc = bundles_to_text(brc)

    # 要約
    result = analyze_digest(GOOGLE_API_KEY, text_24, text_rc, HOURS_RECENT, GEMINI_MODEL)

    POST_MODE = os.getenv("DISCORD_POST_MODE", "markdown")  # 'markdown' | 'embed'
    md = as_markdown(result, dtfmt(now), HOURS_RECENT)

    if POST_MODE == "embed":
        # 画像なしのテキストEmbed（シンプル）
        import requests
        payload = {
            "embeds": [{
                "title": "AIまとめ",
                "description": md[:4000],  # Discord埋め込みの制限に合わせて丸め
                "footer": {"text": "crypto-digest · automated"}
            }]
        }
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        if r.status_code >= 300:
            print(f"[warn] discord status={r.status_code}")
    else:
        # 既存のMarkdown投稿（テキストのみ）
        post_markdown(DISCORD_WEBHOOK_URL, md)
    if not QUIET:
        print(f"[ok] posted digest. 24h={len(msgs_24)} recent={len(msgs_recent)}")

    # 状態保存（必要に応じて拡張）
    state = {"last_run": dtfmt(now), "counts": {"24h": len(msgs_24), "recent": len(msgs_recent)}}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def asyncio_run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)

if __name__ == "__main__":
    main()