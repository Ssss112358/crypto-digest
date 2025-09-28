from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List

import regex as re


@dataclass
class Candidate:
    type: str
    project: str
    action: str
    text: str
    time_wib: str
    score: float
    source_idx: int
    tags: List[str]


def _guess_project(text: str) -> str:
    match = re.search(r"(?:#|＠|@)?([A-Za-z][\w\-]{2,24})", text)
    return match.group(1) if match else ""


def extract_candidates(messages: List[Dict[str, Any]], glossary: Dict[str, Any]) -> List[Candidate]:
    result: List[Candidate] = []
    deny = glossary.get("deny")
    cues = glossary.get("cues", {})

    for idx, message in enumerate(messages):
        text = message.get("text", "") or ""
        if not text:
            continue
        if deny and deny.search(text):
            continue

        base_score = 0.0
        hit_keys: List[str] = []
        for term in glossary.get("terms", []):
            if term["rx"].search(text):
                hit_keys.append(term["key"])
                base_score += term.get("weight", 1.0)

        tags: List[str] = []
        bonus = 0.0
        if cues.get("howto") and cues["howto"].search(text):
            tags.append("howto")
            bonus += 0.6
        if cues.get("lesson") and cues["lesson"].search(text):
            tags.append("lesson")
            bonus += 0.5
        if cues.get("speculation") and cues["speculation"].search(text):
            tags.append("speculation")
            bonus += 0.4
        if cues.get("risk") and cues["risk"].search(text):
            tags.append("risk")
            bonus += 0.6

        if not hit_keys and not tags:
            continue

        action = ""
        for verb in glossary.get("verbs", []):
            if verb["rx"].search(text):
                action = verb["name"]
                break

        def pick_type(keys: List[str], tag_list: List[str]) -> str:
            if "risk" in tag_list:
                return "risk"
            if any(tag in tag_list for tag in ("howto", "lesson", "speculation")):
                return "tip"
            priority = [
                "sale",
                "airdrop",
                "mint",
                "stake",
                "kyc",
                "waitlist",
                "risk",
                "tips",
            ]
            for key in priority:
                if key in keys:
                    return "tip" if key == "tips" else key
            return "other"

        candidate_type = pick_type(hit_keys, tags)
        if candidate_type == "other" and tags:
            candidate_type = "tip"

        score = base_score + bonus

        result.append(
            Candidate(
                type=candidate_type,
                project=_guess_project(text),
                action=action,
                text=text.strip().replace("\n", " ")[:160],
                time_wib=message.get("time_wib", "") or "",
                score=score,
                source_idx=idx,
                tags=tags,
            )
        )

    dedup: Dict[tuple, Candidate] = {}
    for cand in result:
        key = (cand.type, cand.project or cand.text[:30])
        existing = dedup.get(key)
        if not existing or cand.score > existing.score:
            dedup[key] = cand

    return sorted(dedup.values(), key=lambda c: (-c.score, c.project))
