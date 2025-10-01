from __future__ import annotations
import google.generativeai as genai
import logging
from typing import Dict, Any
from .json_utils import safe_json_loads
from .prompts import DIGEST_PROMPT
from src.telegram_pull import fetch_messages_smart
import asyncio
from datetime import datetime, timedelta, timezone
from src.rules import tag_message

def load_msgs(hours_24: int, context_window_days: int, specs: List[str], string_session: str, api_id: int, api_hash: str) -> List[Dict[str, Any]]:
    # 過去 context_window_days 分のメッセージをロード
    # fetch_messages_smart は hours を引数にとるので、context_window_days * 24 を渡す
    total_hours = max(hours_24, context_window_days * 24)
    return asyncio.run(fetch_messages_smart(total_hours, specs, string_session, api_id, api_hash))

def setup_gemini(api_key: str, model: str = "models/gemini-2.0-flash"):    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model, generation_config={
        "temperature": 0.2,
        "max_output_tokens": 8192
    })

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
    return "\\n\\n---\\n\\n".join(summaries)

def chunk_by_time(messages: List[Dict[str, Any]], max_chunk_size: int = 50) -> List[List[Dict[str, Any]]]:
    # TODO: トークン数に基づいてチャンクに分割するロジックを実装
    # 現状はメッセージの数に基づいて仮実装
    chunks = []
    current_chunk = []
    for msg in messages:
        current_chunk.append(msg)
        if len(current_chunk) >= max_chunk_size:
            chunks.append(current_chunk)
            current_chunk = []
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

def prepass_enrich(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched_messages = []
    for msg in messages:
        # メッセージにタグ付け
        msg['tags'] = tag_message(msg)

        # TODO: 時刻正規化、数値抽出、用語保全のロジックを追加
        # 現状はタグ付けのみ
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

def analyze_digest(api_key: str, hours_24: int, context_window_days: int, specs: List[str], string_session: str, api_id: int, api_hash: str, gemini_model: str) -> str:
    # 1. load_msgs
    all_msgs = load_msgs(hours_24, context_window_days, specs, string_session, api_id, api_hash)

    # 2. prepass_enrich
    enriched_msgs = prepass_enrich(all_msgs)

    # 3. chunk_by_time
    # TODO: max_tokens を適切に設定する
    chunks = chunk_by_time(enriched_msgs, max_chunk_size=50) # 仮のmax_chunk_size

    summaries = []
    model = setup_gemini(api_key, gemini_model)

    for chunk in chunks:
        # 各チャンクから text_24h と text_recent を生成
        # TODO: チャンクの期間に応じて text_24h と text_recent を適切に生成する
        # 現状はチャンク内の全メッセージを text_24h と text_recent として扱う
        text_24h_chunk = build_prompt_corpus(chunk)
        text_recent_chunk = build_prompt_corpus(chunk) # 仮に同じものを使用

        prompt = build_prompt(text_24h_chunk, text_recent_chunk, hours_24) # recent_hours は hours_24 を仮に使用
        resp = model.generate_content(prompt)
        if resp.text:
            summaries.append(resp.text.strip())
        else:
            logging.warning("LLM returned empty response for a chunk.")
            summaries.append("（LLMからの応答がありませんでした。）")

    # 4. concat
    return concat(summaries)