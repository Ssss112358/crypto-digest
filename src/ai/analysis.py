from __future__ import annotations
import json
import logging
from typing import Any, Dict, Iterable, Sequence

import google.generativeai as genai

from .json_utils import safe_json_loads
from .prompts import DIGEST_PROMPT, DIGEST_PROMPT_70_20_10_TIPS


def setup_gemini(api_key: str, model: str = "models/gemini-2.0-flash"):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model,
        generation_config={
            "response_mime_type": "application/json",
            "temperature": 0.2,
            "max_output_tokens": 8192,
        },
    )


def build_prompt(text_24h: str, text_recent: str, recent_hours: int) -> str:
    schema_prompt = r"""
【返却JSONスキーマ（厳守・キー省略可・ハルシネーション禁止）】
{
 "sales_airdrops": [{"project": "", "what": "", "action": "", "requirements": "", "wib": "", "confidence": "", "evidence_ids": []}],
 "pipeline": [{"due": "YYYY-MM-DD HH:MM", "tz": "UTC", "item": "", "action": "", "requirements": "", "confidence": "", "evidence_ids": []}],
 "act_now": [{"do": "", "why": "", "evidence_ids": []}],
 "earn_to_prepare": [{"tip": "", "evidence_ids": []}],
 "risks": [{"note": "", "evidence_ids": []}],
 "market_pulse": ["段落1", "段落2"],
 "capsules": [{"topic": "", "text": "", "evidence_ids": []}]
}
"""
    sections = [
        DIGEST_PROMPT.strip(),
        schema_prompt.strip(),
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
    resp = model.generate_content(prompt)
    try:
        return safe_json_loads((resp.text or "").strip())
    except Exception as exc:
        logging.warning("Failed to parse JSON on primary attempt: %s", exc)
        logging.warning("LLM response (primary):\n---\n%s\n---", resp.text)
        resp2 = model.generate_content(prompt + "\n\nJSONのみで再出力してください。")
        try:
            return safe_json_loads((resp2.text or "").strip())
        except Exception as exc2:
            logging.error("Failed to parse JSON on retry: %s", exc2)
            logging.error("LLM response (retry):\n---\n%s\n---", resp2.text)
            return {
                "overall_24h": {"summary": "（LLM要約失敗につき簡易）", "top_entities": [], "events": []},
                "delta_recent": {"window_hours": recent_hours, "new_topics": [], "updates": [], "deadlines": []},
            }


def build_prompt_digest_v21(prompt_template: str, candidates: Sequence[Any], messages: Sequence[Dict[str, Any]], evidence_limit: int = 40) -> str:
    def pick(cands: Iterable[Any], allowed: set[str], top_n: int) -> list[Any]:
        subset = [c for c in cands if c.type in allowed]
        return subset[:top_n]

    sales_like = pick(candidates, {"sale", "airdrop", "mint", "stake", "kyc", "waitlist"}, 120)
    pipeline = pick(candidates, {"sale", "airdrop", "mint"}, 60)
    tips = pick(candidates, {"tip"}, 80)
    risks = pick(candidates, {"risk"}, 40)

    merged = [*sales_like, *pipeline, *tips, *risks]

    def serialize_candidate(obj: Any) -> dict[str, Any]:
        if hasattr(obj, "__dict__"):
            return dict(vars(obj))
        if isinstance(obj, dict):
            return dict(obj)
        return dict(obj)

    cand_json = json.dumps([serialize_candidate(c) for c in merged], ensure_ascii=False)

    evidence = []
    for cand in merged[:evidence_limit]:
        idx = getattr(cand, "source_idx", None)
        if idx is None or idx >= len(messages):
            continue
        msg = messages[idx]
        evidence.append(
            {
                "sender": msg.get("sender") or msg.get("from") or "",
                "text": (msg.get("text") or "")[:200],
            }
        )
    evidence_json = json.dumps(evidence, ensure_ascii=False)

    return (
        f"{prompt_template.strip()}\n\n"
        f"# 候補(JSON)\n{cand_json}\n\n"
        f"# 参照ログ（少数）\n{evidence_json}\n"
    )


def generate_markdown(api_key: str, prompt: str, model_id: str, temperature: float = 0.3, top_k: int | None = None) -> str:
    genai.configure(api_key=api_key)
    config: Dict[str, Any] = {
        "response_mime_type": "text/plain",
        "temperature": temperature,
    }
    if top_k is not None:
        config["top_k"] = top_k
    model = genai.GenerativeModel(model_id, generation_config=config)
    response = model.generate_content(prompt)
    return (response.text or "").strip()
