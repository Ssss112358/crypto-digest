from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Sequence

import re

import google.generativeai as genai
import yaml

from .prompts import DIGEST_PROMPT_V225_STORY


DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_yaml(name: str) -> Dict[str, Any]:
    path = Path(name)
    if not path.is_absolute():
        path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(name)
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def append_dictionary_sections(prompt: str) -> str:
    result = prompt

    try:
        alias_doc = load_yaml("aliases.yml")
        alias_lines: list[str] = []
        for key, value in (alias_doc.get("aliases") or {}).items():
            if not key or not value:
                continue
            alias_lines.append(f"- {key}: {value}")
        for chain in alias_doc.get("chains") or []:
            if chain:
                alias_lines.append(f"- チェーン: {chain}")
        if alias_lines:
            snippet = "\n".join(alias_lines[:50])
            result += "\n\n# エイリアス\n" + snippet
    except FileNotFoundError:
        pass
    except Exception:
        pass

    try:
        gloss_doc = load_yaml("glossary.yml")
        items: list[str] = []
        if isinstance(gloss_doc, dict):
            if "terms" in gloss_doc:
                for entry in gloss_doc.get("terms", []) or []:
                    if not isinstance(entry, dict):
                        continue
                    key = entry.get("key")
                    desc = entry.get("desc") or entry.get("description")
                    synonyms = entry.get("synonyms") or []
                    detail = desc or ", ".join(str(s) for s in synonyms[:5] if s)
                    if key:
                        if detail:
                            items.append(f"- {key}: {detail}")
                        else:
                            items.append(f"- {key}")
            else:
                for key, value in gloss_doc.items():
                    if isinstance(value, dict):
                        desc = value.get("desc") or value.get("description") or ""
                        items.append(f"- {key}: {desc}")
                    else:
                        items.append(f"- {key}: {value}")
        if items:
            result += "\n\n# 用語メモ\n" + "\n".join(items[:50])
    except FileNotFoundError:
        pass
    except Exception:
        pass

    return result


def build_story_prompt(
    story_materials: Sequence[str],
    sales_lines: Sequence[str],
    pipeline_lines: Sequence[str],
    act_now_lines: Sequence[str],
    risk_lines: Sequence[str],
    market_notes: Sequence[str],
    text_24h: str,
    text_recent: str,
    recent_hours: int,
) -> str:
    def _section(title: str, rows: Sequence[str]) -> str:
        rows = [row for row in rows if row]
        if not rows:
            return ""
        return "\n".join([title, *rows])

    prompt = append_dictionary_sections(DIGEST_PROMPT_V225_STORY.strip())

    sections: list[str] = [prompt]
    sections.append(_section("## ストーリー素材", story_materials))
    sections.append(_section("## セール/アクション候補", sales_lines))
    sections.append(_section("## パイプライン候補", pipeline_lines))
    sections.append(_section("## 即応アクション候補", act_now_lines))
    sections.append(_section("## リスク候補", risk_lines))
    sections.append(_section("## Market Pulse用メモ", market_notes))
    sections.append("## 入力ログ")
    sections.append("### 過去24時間のイベント一覧")
    sections.append((text_24h or "").strip())
    sections.append(f"### 直近{recent_hours}時間の重点イベント")
    sections.append((text_recent or "").strip())

    return "\n\n".join(part for part in sections if part)


def build_prompt(text_24h: str, text_recent: str, recent_hours: int) -> str:
    return build_story_prompt(
        story_materials=[],
        sales_lines=[],
        pipeline_lines=[],
        act_now_lines=[],
        risk_lines=[],
        market_notes=[],
        text_24h=text_24h,
        text_recent=text_recent,
        recent_hours=recent_hours,
    )


def build_prompt_digest_v21(
    prompt_template: str,
    candidates: Sequence[Any],
    messages: Sequence[dict[str, Any]],
    evidence_limit: int = 40,
) -> str:
    def serialize_candidate(obj: Any) -> dict[str, Any]:
        if hasattr(obj, "__dict__"):
            return dict(vars(obj))
        if isinstance(obj, dict):
            return dict(obj)
        return dict(obj)

    cand_list = list(candidates)
    cand_json = json.dumps([serialize_candidate(c) for c in cand_list], ensure_ascii=False)

    evidence = []
    for cand in cand_list[:evidence_limit]:
        idx = getattr(cand, "source_idx", None)
        if idx is None or idx >= len(messages):
            continue
        msg = messages[idx]
        evidence.append(
            {
                "sender": msg.get("sender") or msg.get("from") or "",
                "text": (msg.get("text") or "")[:200],
                "time_utc": msg.get("date", ""),
                "time_wib": msg.get("time_wib", ""),
            }
        )
    evidence_json = json.dumps(evidence, ensure_ascii=False)

    return (
        f"{prompt_template.strip()}\n\n"
        f"# 候補(JSON)\n{cand_json}\n\n"
        f"# 参照ログ（少数）\n{evidence_json}\n"
    )


def generate_markdown(
    api_key: str,
    prompt: str,
    model_id: str,
    temperature: float = 0.3,
    top_k: int | None = None,
    max_output_tokens: int | None = None,
) -> str:
    genai.configure(api_key=api_key)
    config: Dict[str, Any] = {
        "response_mime_type": "text/plain",
        "temperature": temperature,
    }
    if top_k is not None:
        config["top_k"] = top_k
    if max_output_tokens is not None:
        config["max_output_tokens"] = max_output_tokens
    model = genai.GenerativeModel(model_id, generation_config=config)
    response = model.generate_content(prompt)
    return (response.text or "").strip()


SUBJECT_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/]{1,20}")
JP_WORD_RE = re.compile(r"[一-龯ぁ-んァ-ヶ]{2,}")


def _build_alias_lookup(alias_map: Dict[str, Any] | None) -> tuple[Dict[str, str], Dict[str, str]]:
    alias_lookup: Dict[str, str] = {}
    canonical_lookup: Dict[str, str] = {}
    if not isinstance(alias_map, dict):
        return alias_lookup, canonical_lookup

    aliases = alias_map.get("aliases") or {}
    if isinstance(aliases, dict):
        for alias, canonical in aliases.items():
            if not alias or not canonical:
                continue
            alias_norm = str(alias).strip().lower()
            canonical_norm = str(canonical).strip()
            if not alias_norm or not canonical_norm:
                continue
            alias_lookup[alias_norm] = canonical_norm
            canonical_lookup[canonical_norm.lower()] = canonical_norm

    chains = alias_map.get("chains") or []
    for chain in chains:
        if isinstance(chain, str) and chain.strip():
            canonical_lookup[chain.strip().lower()] = chain.strip()

    return alias_lookup, canonical_lookup


def resolve_subject(
    message_index: int,
    messages: Sequence[Dict[str, Any]],
    alias_map: Dict[str, Any] | None = None,
) -> str | None:
    alias_lookup, canonical_lookup = _build_alias_lookup(alias_map)

    def normalize_token(token: str) -> str | None:
        lower = token.lower()
        if lower in alias_lookup:
            return alias_lookup[lower]
        if lower in canonical_lookup:
            return canonical_lookup[lower]
        return None

    start = max(message_index, 0)
    end = max(-1, message_index - 50)
    for idx in range(start, end, -1):
        msg = messages[idx]
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        lowered = text.lower()

        for canon_lower, canonical in canonical_lookup.items():
            if canon_lower and canon_lower in lowered:
                return canonical

        for alias, canonical in alias_lookup.items():
            if alias and alias in lowered:
                return canonical

        for token in SUBJECT_TOKEN_RE.findall(text):
            normalized = normalize_token(token)
            if normalized:
                return normalized
            if token.isupper() and len(token) >= 3:
                return token

        for word in JP_WORD_RE.findall(text):
            normalized = normalize_token(word)
            if normalized:
                return normalized
            if len(word) >= 2:
                return word

    return None


FILLER_END = re.compile(
    r"(情報収集|最新情報を収集|注視|監視|様子見|参加機会を伺え|詳細確認|要確認|要検討|期待)$"
)


def defluff(markdown: str) -> str:
    out_lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if FILLER_END.search(stripped):
            content = re.sub(r"[#\-\*\s•]+", "", stripped)
            has_meaning = bool(re.search(r"[A-Za-z0-9一-龥ぁ-んァ-ヶ]", content))
            if not has_meaning:
                continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()
