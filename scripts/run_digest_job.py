# -*- coding: utf-8 -*-
import os
import sys
import json
import asyncio
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set

from dotenv import load_dotenv

from src.telegram_pull import fetch_messages_smart
from src.rules import tag_message
from src.ai.analysis import analyze_digest
from src.delivery.discord import post_markdown

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"

load_dotenv(ROOT / '.env')

UTC = timezone.utc
WIB_OFFSET = timedelta(hours=7)

CATEGORY_LABELS = {
    "emergency": "🚨 緊急",
    "market_news": "📰 市場ニュース",
    "trading": "📈 トレーディング",
    "sales": "🛒 セール",
    "airdrops": "🎁 エアドロ/リワード",
    "deadlines": "⏰ 締切カレンダー（UTC）",
    "tech_updates": "🧪 テストネット/プロダクト更新",
    "resources": "📚 参考スレッド/資料",
}

CATEGORY_ORDER = [
    "emergency",
    "market_news",
    "trading",
    "sales",
    "airdrops",
    "deadlines",
    "tech_updates",
    "resources",
]


def utcnow() -> datetime:
    return datetime.now(UTC)


def dtfmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def build_evidence_map(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for msg in messages:
        ts = msg.get('date')
        link = msg.get('link')
        if ts and ts not in mapping:
            mapping[ts] = link
    return mapping
def flatten_titles(by_category: Dict[str, Any]) -> Set[str]:
    titles: Set[str] = set()
    if not isinstance(by_category, dict):
        return titles
    for key, entries in by_category.items():
        if key == 'other_topics':
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for field in ('title', 'project', 'item'):
                value = entry.get(field)
                if value:
                    titles.add(value)
                    break
    return titles


def build_prompt_corpus(messages: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for msg in messages:
        text_single = (msg.get("text") or "").replace("\n", " ")
        title = msg.get("chat_title") or msg.get("chat_username") or msg.get("chat") or "unknown"
        base = f"{msg.get('date')} {title}: {text_single}"
        tags = msg.get("tags", {})
        tag_parts: List[str] = []
        categories = tags.get("categories") or []
        if categories:
            tag_parts.append("categories=" + ",".join(categories))
        topics = tags.get("topics") or []
        if topics:
            tag_parts.append("topics=" + ",".join(topics))
        deadline = tags.get("deadline")
        if deadline:
            tag_parts.append("deadline=" + deadline)
        if tag_parts:
            rows.append(base + "\n" + "TAGS: " + "; ".join(tag_parts))
        else:
            rows.append(base)
    return "\n---\n".join(rows)


def format_links(evidence_ids: List[str], evidence_map: Dict[str, str]) -> str:
    pieces: List[str] = []
    for ts in (evidence_ids or [])[:3]:
        hhmm = ts[11:16] if len(ts) >= 16 else ts
        url = evidence_map.get(ts)
        # 時刻も出力しない
    return " ".join(pieces)


def build_deadline_table(rows: List[Dict[str, Any]], evidence_map: Dict[str, str]) -> str:
    if not rows:
        return ""
    header = "| 日時 | 項目 |\n|---|---|"
    lines: List[str] = [header]
    for entry in rows:
        due_raw = entry.get("due") or ""
        tz = entry.get("tz") or "UTC"
        try:
            dt_utc = datetime.strptime(due_raw, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
            wib_str = (dt_utc + WIB_OFFSET).strftime("%H:%M")
            due_display = f"{due_raw} ({tz}) / WIB {wib_str}"
        except Exception:
            due_display = f"{due_raw} ({tz})" if due_raw else "-"
        item = entry.get("item") or entry.get("title") or entry.get("project") or "-"
        links = format_links(entry.get("evidence_ids") or [], evidence_map)
        if links:
            item = f"{item} {links}"
        lines.append(f"| {due_display} | {item} |")
    return "\n".join(lines)


def annotate_title(entry: Dict[str, Any], new_marks: Set[str], update_marks: Set[str], resolved_marks: Set[str]) -> str:
    for key in ("title", "project", "item"):
        value = entry.get(key)
        if not value:
            continue
        labels: List[str] = []
        if value in new_marks:
            labels.append("新規")
        if value in update_marks:
            labels.append("更新")
        if value in resolved_marks:
            labels.append("解消")
        if labels:
            return f"{value} ({'/'.join(labels)})"
        return value
    return entry.get("title") or entry.get("project") or entry.get("item") or "項目"


def build_category_line(category: str, entry: Dict[str, Any], evidence_map: Dict[str, str],
                        new_marks: Set[str], update_marks: Set[str], resolved_marks: Set[str]) -> str:
    title = annotate_title(entry, new_marks, update_marks, resolved_marks)
    what = entry.get("what") or entry.get("reason") or entry.get("signal") or ""
    extras: List[str] = []
    if category == "trading":
        pair = entry.get("pair")
        signal = entry.get("signal")
        if pair or signal:
            extras.append(" ".join(filter(None, [pair, signal])))
    if category == "sales":
        venue = entry.get("venue")
        when = entry.get("when")
        if venue:
            extras.append(f"会場: {venue}")
        if when:
            extras.append(f"時刻: {when}")
    if category == "airdrops":
        action = entry.get("action")
        if action:
            extras.append(f"アクション: {action}")
    detail = " / ".join(filter(None, [what, *extras]))
    links = format_links(entry.get("evidence_ids") or [], evidence_map)
    bullet = f"- {title}"
    if detail:
        bullet += f": {detail}"
    if links:
        bullet += f" {links}"
    return bullet


def build_markdown(now: datetime, result: Dict[str, Any], evidence_map: Dict[str, str], new_titles: Set[str], update_titles: Set[str], resolved_titles: Set[str]) -> str:
    now_wib = now + WIB_OFFSET
    delta = result.get('recent_delta', {})
    header_counts = f"新規: +{len(new_titles)} / 更新: +{len(update_titles)} / 解消: -{len(resolved_titles)}"

    lines: List[str] = []
    lines.append(f"**AIまとめ（{dtfmt(now)} UTC / {(now_wib).strftime('%H:%M')} WIB）**")
    lines.append(header_counts)
    lines.append('')

    overall = result.get('overall_24h', {})
    lines.append("**過去24h 全体要約**")
    summary = overall.get("summary") or "該当データなし"
    lines.append(summary)
    top_entities = overall.get("top_entities") or []
    if top_entities:
        lines.append("上位言及: " + ", ".join(top_entities))
    speakers = overall.get("speakers") or []
    if speakers:
        lines.append("主要発言者: " + ", ".join(f"{item.get('name')}({item.get('count')})" for item in speakers))
    for highlight in overall.get("highlights", []):
        title = highlight.get("title", "ハイライト")
        links = format_links(highlight.get("evidence_ids") or [], evidence_map)
        lines.append(f"- {title} {links}".strip())
    lines.append("")

    by_category = result.get("by_category", {})
    for key in CATEGORY_ORDER:
        entries = by_category.get(key) or []
        if not entries:
            continue
        if key == "deadlines":
            lines.append(f"**{CATEGORY_LABELS[key]}**")
            lines.append(build_deadline_table(entries, evidence_map))
            lines.append("")
            continue
        label = CATEGORY_LABELS.get(key)
        if label:
            lines.append(f"**{label}**")
        for entry in entries:
            lines.append(build_category_line(key, entry, evidence_map, new_titles, update_titles, resolved_titles))
        lines.append("")

    other_topics = by_category.get("other_topics") or []
    if other_topics:
        lines.append("**🧵 その他トピック**")
        lines.append(", ".join(other_topics))
        lines.append("")

    return "\n".join(line for line in lines if line is not None)


def parse_source_specs() -> list[str]:
    specs_env = os.getenv('SOURCE_SPECS', '').strip()
    if specs_env:
        return [s.strip() for s in specs_env.split(',') if s.strip()]

    specs: List[str] = []
    for raw in os.getenv('SOURCE_CHATS', '').split(','):
        token = raw.strip()
        if not token:
            continue
        if token.startswith(('title:', 'title~=', 'link:', 'id:', '@', 'username:')):
            specs.append(token)
        else:
            specs.append(f"username:{token.lstrip('@')}")
    return specs


def build_markdown_v2(now: datetime, result: Dict[str, Any], evidence_map: Dict[str, str]) -> str:
    now_wib = now + WIB_OFFSET
    lines: List[str] = []
    lines.append(f"**AIまとめ（{dtfmt(now)} UTC / {now_wib.strftime('%H:%M')} WIB）**")
    lines.append("")

    # 1) セール/エアドロ
    sales = result.get("sales_airdrops") or []
    if sales:
        lines.append("### セール/エアドロ（最優先）")
        for r in sales:
            segs = []
            if r.get("project"): segs.append(r["project"])
            tail = []
            if r.get("what"): tail.append(r["what"])
            if r.get("action"): tail.append(r["action"])
            if r.get("requirements"): tail.append(f"要件: {r['requirements']}")
            if r.get("wib"): tail.append(f"WIB {r['wib']}")
            if r.get("confidence"): tail.append(f"確度: {r['confidence']}")
            body = " — " + " / ".join(tail) if tail else ""
            # 証跡時刻は必要な場合だけ付けたいが、LLMが入れてこない前提なら省略でOK
            lines.append("- " + " ".join(segs) + body)
        lines.append("")

    # 2) パイプライン
    pipe = result.get("pipeline") or []
    if pipe:
        lines.append("### カタリスト・パイプライン（48h〜2週間）")
        for r in pipe:
            due = r.get("due"); tz = r.get("tz") or "UTC"
            due_disp = f"{due} ({tz})"
            if due:
                try:
                    dt_utc = datetime.strptime(due, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
                    due_disp += f" / WIB {(dt_utc+WIB_OFFSET).strftime('%H:%M')}"
                except Exception:
                    pass
            tail = []
            if r.get("item"): tail.append(r["item"])
            if r.get("action"): tail.append(r["action"])
            if r.get("requirements"): tail.append(f"要件: {r['requirements']}")
            if r.get("confidence"): tail.append(f"確度: {r['confidence']}")
            lines.append(f"- {due_disp} — " + " / ".join(tail))
        lines.append("")

    # 3) いま動け
    act = result.get("act_now") or []
    if act:
        lines.append("### いま動け（Top 5）")
        for r in act[:5]:
            do = r.get("do") or ""
            why = r.get("why") or ""
            line = f"- {do}"
            if why: line += f" — {why}"
            lines.append(line)
        lines.append("")

    # 4) Earn to Prepare
    etp = result.get("earn_to_prepare") or []
    if etp:
        lines.append("### Earn to Prepare（将来配布に効く行動）")
        for r in etp:
            tip = r.get("tip") or ""
            if tip: lines.append(f"- {tip}")
        lines.append("")

    # 5) 注意・リスク
    risks = result.get("risks") or []
    if risks:
        lines.append("### 注意・リスク")
        for r in risks:
            note = r.get("note") or ""
            if note: lines.append(f"- {note}")
        lines.append("")

    # 6) Market Pulse（補足）
    mp = result.get("market_pulse") or []
    if mp:
        lines.append("### Market Pulse（補足）")
        for p in mp[:2]:
            if p: lines.append(p)
        lines.append("")

    # 7) トピック・カプセル
    caps = result.get("capsules") or []
    if caps:
        lines.append("### トピック・カプセル")
        for c in caps:
            t = c.get("topic"); txt = c.get("text")
            if t and txt:
                lines.append(f"- **{t}** — {txt}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    specs = parse_source_specs()
    if not specs:
        print('[fatal] SOURCE_SPECS/SOURCE_CHATS が空です')
        sys.exit(1)

    previous_state = load_state()

    api_id = int(os.getenv('TG_API_ID', '0'))
    api_hash = os.getenv('TG_API_HASH', '')
    string_session = os.getenv('TG_STRING_SESSION', '')
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
                'speakers': [{'name': 'tester', 'count': 5}],
                'highlights': []
            },
            'by_category': {
                'emergency': [],
                'market_news': [],
                'trading': [],
                'sales': [],
                'airdrops': [],
                'deadlines': [],
                'tech_updates': [],
                'resources': [],
                'other_topics': []
            },
            'recent_delta': {
                'window_hours': hours_recent,
                'new_topics': [],
                'updates': [],
                'resolved': []
            }
        }
        markdown = build_markdown(now, dummy, {})
        post_markdown(discord_webhook, markdown)
        return

    msgs_24 = asyncio.run(fetch_messages_smart(hours_24, specs, string_session, api_id, api_hash))
    for msg in msgs_24:
        msg['tags'] = tag_message(msg)

    sale_keywords = ["IDO", "プレセール", "プレマ", "claim", "エアドロ", "ポイント", "ホワイトリスト", "KYC", "ステーク", "Mint", "セール", "ローンチ", "launch", "sale", "airdrop"]
    priority_msgs = [msg for msg in msgs_24 if any(k.lower() in (msg.get('text') or "").lower() for k in sale_keywords)]
    other_msgs = [msg for msg in msgs_24 if not any(k.lower() in (msg.get('text') or "").lower() for k in sale_keywords)]
    msgs_24 = priority_msgs + other_msgs

    counts = Counter(msg.get('chat_title') or msg.get('chat_username') or msg.get('chat') or '' for msg in msgs_24)
    if not quiet:
        print(f'[info] telegram 24h counts: {dict(counts)}')

    now_dt = utcnow()
    cutoff_recent = now_dt - timedelta(hours=hours_recent)
    msgs_recent = [
        msg for msg in msgs_24
        if datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC) >= cutoff_recent
    ]

    text_24 = build_prompt_corpus(msgs_24)
    text_recent = build_prompt_corpus(msgs_recent)

    result = analyze_digest(google_api_key, text_24, text_recent, hours_recent, gemini_model)

    # Fallback mechanism
    summary_is_empty = not result or not any(result.get(k) for k in ["sales_airdrops", "pipeline", "act_now", "market_pulse", "capsules"])
    if summary_is_empty and priority_msgs:
        print("[info] LLM summary was empty, using action-first fallback.")
        lines = ["### セール/エアドロ速報（フォールバック）"]
        for msg in priority_msgs[:20]:
            text = (msg.get("text") or "").replace("\n"," ").strip()
            # 余計な長文は丸める（リンク除去は既に無い設計だが念のため）
            if len(text) > 160: text = text[:157] + "…"
            lines.append(f"- {text}")
        post_markdown(discord_webhook, "\n".join(lines))
        return

    evidence_map = build_evidence_map(msgs_24)

    # 新規（v2描画）
    markdown = build_markdown_v2(now_dt, result, evidence_map)
    post_markdown(discord_webhook, markdown)

    # 旧スキーマに依存するため状態保存は一旦無効化
    # state = {
    #     'timestamp': dtfmt(now_dt),
    #     'by_category': result.get('by_category', {}),
    #     'recent_delta': delta,
    # }
    # save_state(state)

    if not quiet:
        print(f"[ok] posted digest. 24h={len(msgs_24)} recent={len(msgs_recent)}")


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        print('[fatal] run_digest_job failed:', type(exc).__name__, str(exc)[:300])
        traceback.print_exc()
        sys.exit(1)
