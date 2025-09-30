from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from ..extract import TopicBundle


@dataclass
class StorySeed:
    topic: str
    timeline: List[str]
    why: str
    impact: str
    next: str
    score: float


def _format_timeline_line(text: str, time_utc: str) -> str:
    snippet = text.strip()
    if len(snippet) > 200:
        snippet = snippet[:197] + "…"
    if time_utc:
        return f"- {time_utc}: {snippet}"
    return f"- {snippet}"


def _derive_hint(tags: set[str], kind: str, field: str) -> str:
    if field == "why":
        if "actionable" in tags:
            return "参加アクションと回収判断が焦点"
        if "numeric" in tags:
            return "具体的な数値や条件が共有"
        if "market" in tags:
            return "地合い変化の兆しを議論"
    if field == "impact":
        if "risk" in tags:
            return "警戒要素が拡散"
        if "market" in tags:
            return "市場全体のムードに影響"
    if field == "next":
        if "absolute_date" in tags:
            return "日程・締切の確認が必要"
        if "actionable" in tags:
            return "次のステップを決める準備"
    return ""


def build_story_seeds(
    topics: Sequence[TopicBundle],
    max_topics: int = 8,
    max_timeline: int = 4,
) -> List[StorySeed]:
    seeds: List[StorySeed] = []

    for bundle in topics[:max_topics]:
        timeline = [
            _format_timeline_line(msg.clean_text, msg.time_utc)
            for msg in bundle.messages[:max_timeline]
        ]
        tags = {tag for msg in bundle.messages for tag in msg.tags}
        why_hint = _derive_hint(tags, bundle.messages[0].kind if bundle.messages else "", "why")
        impact_hint = _derive_hint(tags, bundle.messages[0].kind if bundle.messages else "", "impact")
        next_hint = _derive_hint(tags, bundle.messages[0].kind if bundle.messages else "", "next")
        seeds.append(
            StorySeed(
                topic=bundle.name,
                timeline=timeline,
                why=why_hint,
                impact=impact_hint,
                next=next_hint,
                score=bundle.score,
            )
        )

    return seeds
