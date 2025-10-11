from __future__ import annotations
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
import logging
from typing import Dict, Any, List, List
import json # jsonモジュールを直接使用
import json # jsonモジュールを直接使用

class GeminiQuotaExceededError(RuntimeError):
    """Raised when Gemini API quota is exhausted."""
    pass


from .json_utils import safe_json_loads # safe_json_loads は COMPOSE ステップで必要になる可能性があるので残す
from .prompts import ANALYZE_PROMPT, COMPOSE_PROMPT
from src.telegram_pull import fetch_messages_smart
from src.rules import tag_message
import asyncio
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

RENDER_CONFIG = {
    'style': 'paragraph',
    'force_sections': ['Now', 'Heads-up', 'Context', 'その他'],
    'always_emit_others': True,
    'chunk_limit': 1900,
    'header_template': '6hダイジェスト | 窓口: {start}-{end} WIB',
}

def extract_first_json_block(text: str) -> str | None:
    if not text:
        return None
    m_open = text.find('{')
    m_close = text.rfind('}')
    if m_open == -1 or m_close == -1 or m_close <= m_open:
        return None
    return text[m_open:m_close + 1]


def normalize_thread(t: dict) -> dict:
    t.setdefault('thread_id', t.get('thread_id') or 'auto_{}'.format(abs(hash(t.get('title', '')))))
    t.setdefault('title', t.get('title') or 'Auto Fallback')
    t.setdefault('entity_refs', list(t.get('entity_refs') or []))
    t.setdefault('messages', list(t.get('messages') or []))
    t.setdefault('facts', list(t.get('facts') or []))
    t.setdefault('notes', list(t.get('notes') or []))
    t.setdefault('risks', list(t.get('risks') or []))
    t.setdefault('section_hint', t.get('section_hint') or 'その他')
    if not t.get('mention_count'):
        t['mention_count'] = len(t['messages'])
    time_range = t.get('time_range') or {}
    if not time_range:
        time_range = {}
    if not time_range.get('start_wib'):
        time_range['start_wib'] = _infer_time_boundary(t['messages'], True)
    if not time_range.get('end_wib'):
        time_range['end_wib'] = _infer_time_boundary(t['messages'], False)
    t['time_range'] = time_range
    return t




def _infer_time_boundary(messages: list[dict], first: bool) -> str | None:
    times: list[str] = []
    for msg in messages:
        time_wib = msg.get('time_wib') or ''
        if isinstance(time_wib, str) and len(time_wib) >= 5 and time_wib[2] == ':':
            times.append(time_wib[:5])
    if not times:
        return None
    return min(times) if first else max(times)

def make_min_thread_from_raw(raw_msgs: list[dict]) -> dict:
    take = raw_msgs[:80]
    messages = [{
        'msg_id': str(m.get('id', '')),
        'time_wib': (m.get('time_short') or m.get('date') or '')[11:16] if (m.get('date') and len(m.get('date')) >= 16) else (m.get('time_short') or ''),
        'text': (m.get('text') or '')[:500]
    } for m in take]
    thread = normalize_thread({
        'thread_id': 'auto_fallback_1',
        'title': 'Auto fallback: parse failure',
        'entity_refs': [],
        'messages': messages,
        'facts': [],
        'notes': [],
        'risks': [],
        'section_hint': 'その他',
        'mention_count': len(messages),
        'time_range': {
            'start_wib': _infer_time_boundary(messages, True),
            'end_wib': _infer_time_boundary(messages, False),
        },
    })
    return {
        'threads': [thread],
        'entities': [],
        'meta': {'fallback': True},
    }


def safe_parse_analysis(text: str, raw_msgs: list[dict]) -> dict:
    blk = extract_first_json_block(text or '')
    if blk:
        try:
            data = json.loads(blk)
            threads = data.get('threads') or []
            if not threads:
                return make_min_thread_from_raw(raw_msgs)
            data['threads'] = [normalize_thread(t) for t in threads]
            return data
        except Exception:
            pass
    return make_min_thread_from_raw(raw_msgs)


def load_msgs(hours_24: int, context_window_days: int, specs: List[str], string_session: str, api_id: int, api_hash: str) -> List[Dict[str, Any]]:
    # 過去 context_window_days 分のメッセージをロード
    # fetch_messages_smart は hours を引数にとるので、context_window_days * 24 を渡す
    total_hours = max(hours_24, context_window_days * 24)
    return asyncio.run(fetch_messages_smart(total_hours, specs, string_session, api_id, api_hash))

def setup_gemini(api_key: str, model: str = "models/gemini-2.0-flash", response_mime_type: str = None):
    genai.configure(api_key=api_key)
    config = {
        "temperature": 0.2,
        "max_output_tokens": 8192
    }
    if response_mime_type:
        config["response_mime_type"] = response_mime_type
    return genai.GenerativeModel(model, generation_config=config)

def build_prompt(text_24h: str, text_recent: str, recent_hours: int) -> str:
    sections = [
        DIGEST_PROMPT.strip(),
        "## 入力データ",
        "### 過去24時間のイベント一覧",
        (text_24h or "").strip(),
        f"### 直近{recent_hours}時間の重点イベント",
        (text_recent or "").strip(),
    ]
    return "\n\n".join(part for part in sections if part)

def concat(summaries: List[str]) -> str:
    if not summaries:
        return ""

    # 重複する見出しを削除し、区切りを変更
    processed_summaries = []
    first_summary = summaries[0]
    processed_summaries.append(first_summary)

    for i in range(1, len(summaries)):
        current_summary = summaries[i]
        # 2つ目以降のチャンクから、すべての見出し（# で始まる行）を削除
        lines = current_summary.split('\n')
        filtered_lines = [line for line in lines if not line.strip().startswith('#')]
        processed_summaries.append("\n".join(filtered_lines).strip())

    return "\n\n（ここから下は詳細）\n\n".join(processed_summaries)

def chunk_by_time(messages: List[Dict[str, Any]], max_tokens: int = 4000) -> List[List[Dict[str, Any]]]:
    # トークン数に基づいてチャンクに分割するロジック
    # 簡易的に文字数（バイト数）をトークン数の代わりとして使用
    chunks = []
    current_chunk = []
    current_chunk_tokens = 0

    for msg in messages:
        # processed_text が存在すればそれを使用、なければ元のtextを使用
        msg_text = msg.get('processed_text') or msg.get('text', '')
        msg_tokens = len(msg_text.encode('utf-8')) # バイト数をトークン数の代わりとする

        if current_chunk_tokens + msg_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_chunk_tokens = 0

        current_chunk.append(msg)
        current_chunk_tokens += msg_tokens

    if current_chunk:
        chunks.append(current_chunk)
    return chunks

def prepass_enrich(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched_messages = []
    for msg in messages:
        # メッセージにタグ付け
        msg['tags'] = tag_message(msg)

        processed_text = msg.get('text', '')

        # 時刻正規化 (例: "今夜" -> 特定のWIB時刻) - これはAIに任せる部分もあるため、ここでは簡易的に
        # TODO: より高度な時刻正規化ロジックを実装
        # 現状はAIがプロンプトで処理することを期待

        # 数値の保全 (例: "2%" -> "2パーセント" またはそのまま)
        # AIが数字を抽象化しないよう、プロンプトで指示済み。ここでは特に変更しない。

        # 用語保全 (例: "直コン" -> "直接コントラクト", "FCFS" -> "先着順")
        # AIがプロンプトで処理することを期待するが、ここでは簡易的な置換を試みる
        replacements = {
            "直コン": "直接コントラクト",
            "FCFS": "先着順 (First-Come, First-Served)",
            "WL": "ホワイトリスト",
            "KYC": "本人確認 (Know Your Customer)",
            "FDV": "完全希薄化評価額 (Fully Diluted Valuation)",
            "MC": "時価総額 (Market Cap)",
            "YB": "YieldBasis", # 固有名詞の正規化
            "YieldBasis": "YieldBasis", # 念のため
            "EdgeX": "Edgex", # 固有名詞の正規化
            "Edgex": "Edgex", # 念のため
        }
        for old, new in replacements.items():
            # 大文字小文字を区別しない置換を行うために正規表現を使用
            processed_text = re.sub(r'\b' + re.escape(old) + r'\b', new, processed_text, flags=re.IGNORECASE)

        msg['processed_text'] = processed_text # 処理済みのテキストを新しいキーに保存
        enriched_messages.append(msg)
    return enriched_messages

def build_prompt_corpus(messages: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for msg in messages:
        text_single = (msg.get("text") or "").replace("\\n", " ")
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
            rows.append(base + "\\n" + "TAGS: " + "; ".join(tag_parts))
        else:
            rows.append(base)
    return "\\n---\\n".join(rows)

def _time_to_minutes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        hours, minutes = value.split(':', 1)
        return int(hours) * 60 + int(minutes)
    except Exception:
        return None


def _merge_time_range(current: dict | None, incoming: dict | None) -> dict:
    result = dict(current or {})
    incoming = incoming or {}
    start_candidates = [result.get('start_wib'), incoming.get('start_wib')]
    valid_starts = [v for v in start_candidates if _time_to_minutes(v) is not None]
    if valid_starts:
        result['start_wib'] = min(valid_starts, key=_time_to_minutes)
    elif incoming.get('start_wib') and not result.get('start_wib'):
        result['start_wib'] = incoming.get('start_wib')

    end_candidates = [result.get('end_wib'), incoming.get('end_wib')]
    valid_ends = [v for v in end_candidates if _time_to_minutes(v) is not None]
    if valid_ends:
        result['end_wib'] = max(valid_ends, key=_time_to_minutes)
    elif incoming.get('end_wib') and not result.get('end_wib'):
        result['end_wib'] = incoming.get('end_wib')
    return result


def merge_analysis_results(results: list[dict]) -> dict:
    merged_entities: dict[str, dict] = {}
    threads_index: dict[tuple, dict] = {}

    for res in results:
        for entity in res.get('entities', []):
            canonical = entity.get('canonical')
            if not canonical:
                continue
            stored = merged_entities.setdefault(canonical, dict(entity))
            if stored is entity:
                continue
            aliases = list(stored.get('aliases', []))
            for alias in entity.get('aliases', []):
                if alias not in aliases:
                    aliases.append(alias)
            stored['aliases'] = aliases

        for thread in res.get('threads', []):
            normalized = normalize_thread(thread)
            entity_refs = tuple(sorted(normalized.get('entity_refs') or []))
            section_key = normalized.get('section_hint') or 'その他'
            title_key = (normalized.get('title') or '').strip().lower()
            key = (entity_refs, section_key, title_key)

            existing = threads_index.get(key)
            if not existing:
                new_thread = {
                    'thread_id': normalized.get('thread_id') or 'thread_{}'.format(len(threads_index) + 1),
                    'title': normalized.get('title'),
                    'entity_refs': list(normalized.get('entity_refs', [])),
                    'messages': list(normalized.get('messages', [])),
                    'facts': list(normalized.get('facts', [])),
                    'notes': list(normalized.get('notes', [])),
                    'risks': list(normalized.get('risks', [])),
                    'section_hint': section_key,
                    'mention_count': normalized.get('mention_count') or len(normalized.get('messages', [])),
                    'time_range': dict(normalized.get('time_range') or {}),
                }
                threads_index[key] = new_thread
                continue

            existing.setdefault('messages', []).extend(normalized.get('messages', []))
            existing.setdefault('facts', []).extend(normalized.get('facts', []))
            existing.setdefault('notes', []).extend(normalized.get('notes', []))
            existing.setdefault('risks', []).extend(normalized.get('risks', []))

            refs = existing.setdefault('entity_refs', [])
            for ref in normalized.get('entity_refs', []):
                if ref not in refs:
                    refs.append(ref)

            existing['mention_count'] = existing.get('mention_count', 0) + (
                normalized.get('mention_count') or len(normalized.get('messages', []))
            )
            existing['time_range'] = _merge_time_range(existing.get('time_range'), normalized.get('time_range'))

    meta = results[0].get('meta', {}) if results else {}
    meta['generated_at'] = datetime.now(timezone.utc).isoformat()

    merged_threads = [normalize_thread(thread) for thread in threads_index.values()]

    return {
        'meta': meta,
        'entities': list(merged_entities.values()),
        'threads': merged_threads,
    }

def _merge_entities(results: list[dict]) -> list[dict]:
    merged_entities: dict[str, dict] = {}
    for res in results:
        for entity in res.get("entities", []) or []:
            canonical = entity.get("canonical")
            if not canonical:
                continue

            current = merged_entities.get(canonical)
            if current is None:
                base = dict(entity)
                base["aliases"] = list(entity.get("aliases") or [])
                merged_entities[canonical] = base
                continue

            aliases = current.setdefault("aliases", [])
            for alias in entity.get("aliases", []) or []:
                if alias not in aliases:
                    aliases.append(alias)

            for key, value in entity.items():
                if key not in current:
                    current[key] = value
    return list(merged_entities.values())

def analyze_digest(api_key: str, hours_24: int, hours_recent: int, context_window_days: int, specs: List[str], string_session: str, api_id: int, api_hash: str, gemini_model: str, digest_mode: str = 'lossless') -> str:
    # 1. load_msgs (過去 context_window_days 分のメッセージをロード)
    all_msgs = load_msgs(hours_24, context_window_days, specs, string_session, api_id, api_hash)

    # 2. prepass_enrich (タグ付け、用語保全など)
    enriched_msgs = prepass_enrich(all_msgs)

    # 3. chunk_by_time (メッセージをチャンクに分割)
    # TODO: max_tokens を適切に設定する
    chunks = chunk_by_time(enriched_msgs, max_tokens=4000) # 仮のmax_tokens

    # ANALYZE ステップ
    analysis_results = []
    analyze_model = setup_gemini(api_key, gemini_model, response_mime_type="application/json")

    now_dt = datetime.now(timezone.utc)
    for i, chunk in enumerate(chunks):
        # チャンク内のメッセージを時間でフィルタリングして text_24h と text_recent を生成
        cutoff_24h = now_dt - timedelta(hours=hours_24)
        msgs_24h_in_chunk = [
            msg for msg in chunk
            if datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) >= cutoff_24h
        ]
        text_24h_chunk = build_prompt_corpus(msgs_24h_in_chunk)

        cutoff_recent = now_dt - timedelta(hours=hours_recent)
        msgs_recent_in_chunk = [
            msg for msg in chunk
            if datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) >= cutoff_recent
        ]
        text_recent_chunk = build_prompt_corpus(msgs_recent_in_chunk)

        # ANALYZE プロンプト
        analyze_prompt_input = f"{ANALYZE_PROMPT.strip()}\n\n## 入力データ (チャンク {i+1}/{len(chunks)})\n### 過去{hours_24}時間のイベント一覧\n{text_24h_chunk}\n### 直近{hours_recent}時間の重点イベント\n{text_recent_chunk}"
        
        try:
            resp = analyze_model.generate_content(analyze_prompt_input)
        except google_exceptions.ResourceExhausted as exc:
            logging.error("Gemini quota exhausted during analyze chunk %s: %s", i + 1, exc)
            raise GeminiQuotaExceededError("Gemini API quota exhausted") from exc
        parsed_analysis = safe_parse_analysis(resp.text.strip() if resp.text else "", chunk)
        analysis_results.append(parsed_analysis)

    # 複数の analysis_results を統合
    merged_analysis_data = merge_analysis_results(analysis_results)

    # COMPOSE ステップ
    compose_model = setup_gemini(api_key, gemini_model)

    window_start_wib = (now_dt - timedelta(hours=hours_recent)).astimezone(WIB)
    window_end_wib = now_dt.astimezone(WIB)
    compose_payload = {
        'analysis': merged_analysis_data,
        'render_config': RENDER_CONFIG,
        'digest_mode': digest_mode or 'lossless',
        'time_window': {
            'hours_recent': hours_recent,
            'hours_24': hours_24,
            'context_window_days': context_window_days,
            'start_wib': window_start_wib.strftime('%H:%M'),
            'end_wib': window_end_wib.strftime('%H:%M'),
            'start_iso': window_start_wib.isoformat(),
            'end_iso': window_end_wib.isoformat(),
        },
        'source': {
            'specs': specs,
        },
    }

    compose_prompt_input = f"{COMPOSE_PROMPT.strip()}\n\n{json.dumps(compose_payload, ensure_ascii=False, indent=2)}"
    try:
        resp = compose_model.generate_content(compose_prompt_input)
    except google_exceptions.ResourceExhausted as exc:
        logging.error("Gemini quota exhausted during compose step: %s", exc)
        raise GeminiQuotaExceededError("Gemini API quota exhausted") from exc
    if resp.text:
        return resp.text.strip()
    logging.warning("LLM returned empty response for COMPOSE step.")
    return "（LLMからの応答がありませんでした。）"














