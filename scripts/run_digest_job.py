# -*- coding: utf-8 -*-
import os
import sys
import json
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set

from dotenv import load_dotenv

from src.telegram_pull import fetch_messages_smart
from src.rules import tag_message
from src.ai.analysis import analyze_digest, GeminiQuotaExceededError
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
    lines.append(f"**🔶KudasaiJP Telegramまとめ（{dtfmt(now)} UTC / {now_wib.strftime('%H:%M')} WIB）**")
    lines.append("")

    # 1) セール/エアドロ
    sales = result.get("sales_airdrops") or []
    if sales:
        lines.append("### セール/エアドロ（最優先）")
        grouped_sales = defaultdict(list)
        for r in sales:
            project = r.get("project")
            what = r.get("what")
            grouped_sales[(project, what)].append(r)

        for (project, what), entries in grouped_sales.items():
            header_parts = []
            if project: header_parts.append(project)
            if what: header_parts.append(what)
            if header_parts:
                lines.append(f"- {' — '.join(header_parts)}")
            else:
                lines.append("- (不明な項目)")
            for entry in entries:
                tail = []
                if entry.get("action"): tail.append(entry["action"])
                requirements_value = entry.get("requirements")
                if requirements_value and requirements_value != "不明" and requirements_value != "なし":
                    tail.append(f"要件: {requirements_value}")
                wib_value = entry.get("wib")
                if wib_value and wib_value != "不明":
                    tail.append(f"WIB {wib_value}")
                detail_body = " / ".join(tail) if tail else ""
                lines.append(f"  - {detail_body}")
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
            requirements_value = r.get("requirements")
            if requirements_value and requirements_value != "不明" and requirements_value != "なし":
                tail.append(f"要件: {requirements_value}")

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


def build_quota_exceeded_markdown(now: datetime, hours_24: int, hours_recent: int, context_window_days: int) -> str:
    now_wib = now + WIB_OFFSET
    start_wib = (now - timedelta(hours=hours_recent) + WIB_OFFSET).strftime('%H:%M')
    end_wib = now_wib.strftime('%H:%M')
    header = f"**6hダイジェスト | 窓口: {start_wib}-{end_wib} WIB**"

    def footer() -> str:
        return f"（言及×0 / {start_wib}–{end_wib} WIB）"

    lines = [
        header,
        "",
        "## Now",
        "**Gemini API — quota exhausted**",
        "Gemini API のリクエスト上限に達したため、この時間帯の自動配信は一時停止しました。",
        footer(),
        "",
        "## Heads-up",
        "**再試行の目安**",
        "クォータのリセットを待ってから再実行してください。対策として API プランや呼び出し間隔の調整も検討が必要です。",
        footer(),
        "",
        "## Context",
        "該当なし",
        footer(),
        "",
        "## その他",
        "該当なし",
        footer(),
    ]
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
    hours_24 = int(os.getenv('HOURS_24', '6')) # 24h -> 6h
    hours_recent = int(os.getenv('HOURS_RECENT', '6'))
    quiet = os.getenv('QUIET_LOG', '0') == '1'
    dry_run = os.getenv('DRY_RUN', '0') == '1'
    no_filters = os.getenv('NO_FILTERS', '0') == '1'
    render_style = os.getenv('RENDER_STYLE', 'default')
    context_window_days = int(os.getenv('CONTEXT_WINDOW_DAYS', '1'))
    include_evidence_in_output = os.getenv('INCLUDE_EVIDENCE_IN_OUTPUT', '0') == '1'
    digest_mode = (os.getenv('DIGEST_MODE', 'lossless') or 'lossless').strip().lower()
    if digest_mode not in {'lossless', 'compact'}:
        digest_mode = 'lossless'

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

    # analyze_digest の呼び出し
    quota_notice = False
    try:
        markdown = analyze_digest(
            google_api_key,
            hours_24,
            hours_recent,  # hours_recent を追加
            context_window_days,
            specs,
            string_session,
            api_id,
            api_hash,
            gemini_model,
            digest_mode,
        )
    except GeminiQuotaExceededError as exc:
        quota_notice = True
        print(f"[warn] Gemini quota exhausted: {exc}")
        markdown = build_quota_exceeded_markdown(now, hours_24, hours_recent, context_window_days)
    if not markdown.strip(): # 空のMarkdownが返された場合のフォールバック
        print("[info] LLM returned empty narrative, using action-first fallback.")
        markdown = "### セール/エアドロ速報（フォールバック）\n\n（情報なし）"
    post_markdown(discord_webhook, markdown)

    # 旧スキーマに依存するため状態保存は一旦無効化
    # state = {
    #     'timestamp': dtfmt(now_dt),
    #     'by_category': result.get('by_category', {}),
    #     'recent_delta': delta,
    # }
    # save_state(state)

    if not quiet:
        if quota_notice:
            print("[ok] posted quota notice.")
        else:
            print("[ok] posted digest.")


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        print('[fatal] run_digest_job failed:', type(exc).__name__, str(exc)[:300])
        traceback.print_exc()
        sys.exit(1)
