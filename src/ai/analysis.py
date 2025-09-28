from __future__ import annotations
import google.generativeai as genai
from typing import Dict, Any, List
from .json_utils import safe_json_loads
from .prompts import EVENTS_PROMPT, DIGEST_PROMPT
import json


def call_gemini(api_key: str, prompt: str, model_id: str, is_json: bool = True) -> Dict[str, Any] | str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_id, generation_config={
        "response_mime_type": "application/json" if is_json else "text/plain",
        "temperature": 0.2,
        "max_output_tokens": 3500 if is_json else 8192
    })
    
    resp = model.generate_content(prompt) # 1st try
    try:
        if is_json:
            return safe_json_loads((resp.text or "").strip())
        else:
            return (resp.text or "").strip()
    except Exception:
        resp2 = model.generate_content(prompt + ("\n\nJSONのみで再出力してください。" if is_json else "\n\nMarkdownのみで再出力してください。")) # retry
        try:
            if is_json:
                return safe_json_loads((resp2.text or "").strip())
            else:
                return (resp2.text or "").strip()
        except Exception:
            if is_json:
                return [] # イベント抽出失敗時は空リスト
            else:
                return "AI要約生成に失敗しました。" # Markdown生成失敗時はエラーメッセージ


def extract_events(api_key: str, corpus: str, model_id: str) -> List[Dict[str, Any]]:
    prompt = EVENTS_PROMPT + f"\n\nログ:\n{corpus}"
    events = call_gemini(api_key, prompt, model_id, is_json=True)
    if not isinstance(events, list): # JSONパース失敗時など
        return []
    return events


def generate_markdown(api_key: str, events: List[Dict[str, Any]], model_id: str) -> str:
    events_json = json.dumps(events, ensure_ascii=False, indent=2)
    prompt = DIGEST_PROMPT + f"\n\nevents[]:\n{events_json}"
    markdown = call_gemini(api_key, prompt, model_id, is_json=False)
    if not isinstance(markdown, str): # Markdown生成失敗時など
        return "AI要約生成に失敗しました。"
    return markdown