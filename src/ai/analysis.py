from __future__ import annotations
import google.generativeai as genai
import logging
from typing import Dict, Any
from .json_utils import safe_json_loads
from .prompts import DIGEST_PROMPT

def setup_gemini(api_key: str, model: str = "models/gemini-2.0-flash"):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model, generation_config={
        "response_mime_type": "application/json",
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

def analyze_digest(api_key: str, text_24h: str, text_recent: str, recent_hours: int, model_id: str) -> Dict[str, Any]:
    model = setup_gemini(api_key, model_id)
    prompt = build_prompt(text_24h, text_recent, recent_hours)
    # 1st try
    resp = model.generate_content(prompt)
    try:
        return safe_json_loads((resp.text or "").strip())
    except Exception as e:
        logging.warning(f"Failed to parse JSON on 1st try: {e}")
        logging.warning(f"LLM response (1st try):\n---\n{resp.text}\n---")
        # retry
        resp2 = model.generate_content(prompt + "\n\nJSONのみで再出力してください。")
        try:
            return safe_json_loads((resp2.text or "").strip())
        except Exception as e2:
            logging.error(f"Failed to parse JSON on 2nd try: {e2}")
            logging.error(f"LLM response (2nd try):\n---\n{resp2.text}\n---")
            # fallback（空振りゼロ）
            return {
                "overall_24h": {"summary": "（LLM要約失敗につき簡易）", "top_entities": [], "events": []},
                "delta_recent": {"window_hours": recent_hours, "new_topics": [], "updates": [], "deadlines": []}
            }