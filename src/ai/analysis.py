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
import asyncio
from datetime import datetime, timedelta, timezone
from src.rules import tag_message
import re
import json

def extract_first_json_block(text: str) -> str | None:
    # 先頭の { と対応する最後の } を大雑把に拾う
    m_open = text.find('{')
    m_close = text.rfind('}')
    if m_open == -1 or m_close == -1 or m_close <= m_open:
        return None
    return text[m_open:m_close+1]

def make_min_thread_from_raw(raw_msgs: list[dict]) -> dict:
    # raw_msgs: [{"id": "...", "time": "...", "text": "..."} ...] 想定
    take = raw_msgs[:80]  # 念のため上限（爆発防止）
    messages = []
    for m in take:
        messages.append({
            "msg_id": str(m.get("id") or ""),
            "time_wib": m.get("time_short") or m.get("date")[11:16] or "", # time_shortがない場合、dateからHH:MMを抽出
            "text": (m.get("text") or "")[:500]
        })
    return {
        "threads": [{
            "thread_id": "auto_fallback_1",
            "title": "自動フォールバック（解析失敗）",
            "entity_refs": [],
            "messages": messages
        }],
        "entities": [],
        "meta": {"fallback": True}
    }

def safe_parse_analysis(text: str) -> dict:
    block = extract_first_json_block(text or "")
    if block:
        try:
            return json.loads(block)
        except Exception:
            pass
    # 最低限のフォールバック（後述の make_min_thread_from_raw と組で使う）
    return {"threads": [], "entities": [], "meta": {"fallback": True}}

def extract_first_json_block(text: str) -> str | None:
    if not text: return None
    a = text.find('{'); b = text.rfind('}')
    return text[a:b+1] if (a != -1 and b != -1 and b > a) else None

REQUIRED_THREAD_KEYS = ("thread_id", "title", "entity_refs", "messages", "facts")

def normalize_thread(t: dict) -> dict:
    t.setdefault("thread_id", t.get("thread_id") or "auto_"+str(abs(hash(t.get("title","")))) )
    t.setdefault("title", t.get("title") or "Auto Fallback")
    t.setdefault("entity_refs", t.get("entity_refs") or [])
    t.setdefault("messages", t.get("messages") or [])
    t.setdefault("facts", t.get("facts") or [])  # ★必ず存在させる
    return t

def make_min_thread_from_raw(raw_msgs: list[dict]) -> dict:
    take = raw_msgs[:80]
    messages = [{
        "msg_id": str(m.get("id","")),
        "time_wib": (m.get("time_short") or m.get("date") or "")[11:16] if (m.get("date") and len(m.get("date")) >= 16) else (m.get("time_short") or ""),
        "text": (m.get("text") or "")[:500]
    } for m in take]

    doc = {
        "threads": [normalize_thread({
            "thread_id": "auto_fallback_1",
            "title": "自動フォールバック（解析失敗）",
            "entity_refs": [],
            "messages": messages,
            "facts": []  # ★空でも置く
        })],
        "entities": [],
        "meta": {"fallback": True}
    }
    return doc

def safe_parse_analysis(text: str, raw_msgs: list[dict]) -> dict:
    blk = extract_first_json_block(text or "")
    if blk:
        try:
            data = json.loads(blk)
            # threadsが空ならフォールバックに差し替え
            if not data.get("threads"):
                return make_min_thread_from_raw(raw_msgs)
            # threadごとに正規化
            data["threads"] = [normalize_thread(t) for t in data.get("threads", [])]
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

def merge_analysis_results(analysis_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged_entities = {}
    merged_threads = {}
    
    for result in analysis_results:
        # Entities のマージ
        for entity in result.get("entities", []):
            canonical = entity["canonical"]
            if canonical not in merged_entities:
                merged_entities[canonical] = entity
            else:
                # エイリアスを統合
                merged_entities[canonical]["aliases"].extend(
                    [a for a in entity.get("aliases", []) if a not in merged_entities[canonical]["aliases"]]
                )
        
        # Threads のマージ (thread_id または title で統合)
        for thread in result.get("threads", []):
            thread_id = thread.get("thread_id")
            title = thread.get("title")
            
            # 既存のスレッドと類似しているかチェック
            found_match = False
            for existing_thread_id, existing_thread in merged_threads.items():
                # TODO: より高度な類似度判定 (タイトル類似度など)
                # 現状はthread_idが同じか、タイトルが完全に一致する場合のみマージ
                if thread_id and existing_thread_id == thread_id:
                    # メッセージ、facts、risks を統合
                    existing_thread["messages"].extend(thread.get("messages", []))
                    existing_thread["facts"].extend(thread.get("facts", []))
                    existing_thread["risks"].extend(thread.get("risks", []))
                    existing_thread["entity_refs"].extend(
                        [e for e in thread.get("entity_refs", []) if e not in existing_thread["entity_refs"]]
                    )
                    found_match = True
                    break
                elif title and existing_thread.get("title") == title:
                    # メッセージ、facts、risks を統合
                    existing_thread["messages"].extend(thread.get("messages", []))
                    existing_thread["facts"].extend(thread.get("facts", []))
                    existing_thread["risks"].extend(thread.get("risks", []))
                    existing_thread["entity_refs"].extend(
                        [e for e in thread.get("entity_refs", []) if e not in existing_thread["entity_refs"]]
                    )
                    found_match = True
                    break
            
            if not found_match:
                # 新しいスレッドとして追加
                merged_threads[thread_id or title or f"thread_{len(merged_threads)}"] = thread
                
    # 最終的なメタ情報を設定
    meta = analysis_results[0].get("meta", {}) if analysis_results else {}
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()

    return {
        "meta": meta,
        "entities": list(merged_entities.values()),
        "threads": list(merged_threads.values())
    }

def merge_analysis_results(results: list[dict]) -> dict:
    merged = {"threads": [], "entities": [], "meta": {}}
    by_title = {}

    for res in results:
        for th in res.get("threads", []):
            th = normalize_thread(th)  # ★必ず正規化
            key = (th.get("title") or "").strip().lower()
            if key in by_title:
                existing = by_title[key]
                existing.setdefault("messages", []).extend(th.get("messages", []))
                existing.setdefault("entity_refs", []).extend(th.get("entity_refs", []))
                existing.setdefault("facts", []).extend(th.get("facts", []))  # ★ここ
                existing.setdefault("risks", []).extend(th.get("risks", [])) # risksも追加
            else:
                by_title[key] = normalize_thread({
                    "thread_id": th["thread_id"],
                    "title": th["title"],
                    "entity_refs": th.get("entity_refs", []),
                    "messages": th.get("messages", []),
                    "facts": th.get("facts", []),  # ★ここ
                    "risks": th.get("risks", []) # risksも追加
                })

    merged["threads"] = list(by_title.values())
    merged["entities"] = _merge_entities(results)  # 既存関数想定
    
    # 最終的なメタ情報を設定
    meta = results[0].get("meta", {}) if results else {}
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    merged["meta"] = meta

    return merged

def _merge_entities(results: list[dict]) -> list[dict]:
    merged_entities = {}
    for res in results:
        for entity in res.get("entities", []):
            canonical = entity["canonical"]
            if canonical not in merged_entities:
                merged_entities[canonical] = entity
            else:
                merged_entities[canonical]["aliases"].extend(
                    [a for a in entity.get("aliases", []) if a not in merged_entities[canonical]["aliases"]]
                )
    return list(merged_entities.values())

def analyze_digest(api_key: str, hours_24: int, hours_recent: int, context_window_days: int, specs: List[str], string_session: str, api_id: int, api_hash: str, gemini_model: str) -> str:
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
    compose_model = setup_gemini(api_key, gemini_model) # COMPOSEはJSON出力ではないのでresponse_mime_typeは指定しない

    compose_prompt_input = f"{COMPOSE_PROMPT.strip()}\n\n## 入力データ (analysis.json)\n{json.dumps(merged_analysis_data, ensure_ascii=False, indent=2)}"
    try:
        resp = compose_model.generate_content(compose_prompt_input)
    except google_exceptions.ResourceExhausted as exc:
        logging.error("Gemini quota exhausted during compose step: %s", exc)
        raise GeminiQuotaExceededError("Gemini API quota exhausted") from exc
    if resp.text:
        return resp.text.strip()
    else:
        logging.warning("LLM returned empty response for COMPOSE step.")
        return "（LLMからの応答がありませんでした。）"
