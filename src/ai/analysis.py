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
import re

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
        }
        for old, new in replacements.items():
            processed_text = processed_text.replace(old, new)

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

    now_dt = datetime.now(timezone.utc) # 現在時刻をUTCで取得
    for chunk in chunks:
        # チャンク内のメッセージを時間でフィルタリングして text_24h と text_recent を生成
        # 24時間以内のメッセージ
        cutoff_24h = now_dt - timedelta(hours=hours_24)
        msgs_24h_in_chunk = [
            msg for msg in chunk
            if datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) >= cutoff_24h
        ]
        text_24h_chunk = build_prompt_corpus(msgs_24h_in_chunk)

        # 直近のメッセージ (hours_recent は analyze_digest の引数にはないため、hours_24 を仮に使用)
        # TODO: hours_recent を analyze_digest の引数に追加するか、適切な値を設定する
        cutoff_recent = now_dt - timedelta(hours=hours_24) # 仮にhours_24を使用
        msgs_recent_in_chunk = [
            msg for msg in chunk
            if datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) >= cutoff_recent
        ]
        text_recent_chunk = build_prompt_corpus(msgs_recent_in_chunk)

        prompt = build_prompt(text_24h_chunk, text_recent_chunk, hours_24) # recent_hours は hours_24 を仮に使用
        resp = model.generate_content(prompt)
        if resp.text:
            summaries.append(resp.text.strip())
        else:
            logging.warning("LLM returned empty response for a chunk.")
            summaries.append("（LLMからの応答がありませんでした。）")

    # 4. concat
    return concat(summaries)