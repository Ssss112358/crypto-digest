from __future__ import annotations
import google.generativeai as genai
import logging
from typing import Dict, Any
from .json_utils import safe_json_loads
from .prompts import DIGEST_PROMPT

def setup_gemini(api_key: str, model: str = "models/gemini-2.0-flash"):
    genai.configure(api_key=api_key)
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

def analyze_digest(api_key: str, text_24h: str, text_recent: str, recent_hours: int, model_id: str) -> str:
    model = setup_gemini(api_key, model_id)
    prompt = build_prompt(text_24h, text_recent, recent_hours)
    # 1st try
    resp = model.generate_content(prompt)
    if resp.text:
        return resp.text.strip()
    else:
        logging.warning("LLM returned empty response.")
        return "（LLMからの応答がありませんでした。）"