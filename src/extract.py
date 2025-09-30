from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import regex as re


URL_RE = re.compile(r"https?://\S+")
EMOJI_RE = re.compile(r"\p{Emoji_Presentation}|\p{Emoji}\uFE0F?", re.UNICODE)
MEDIA_ONLY_RE = re.compile(r"^\[(?:photo|image|video|voice|sticker|animation)\]$", re.IGNORECASE)
FORWARD_RE = re.compile(r"^(?:Forwarded from|転送元:?|Forwarded message)$", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#[\w\-]+")
ABSOLUTE_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
NUMERIC_DETAIL_RE = re.compile(r"\b\d+(?:\.\d+)?(?:%|x|倍|枚|人|pt|ポイント|usd|usdt|eth|sol|btc|k|m|b)?\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[\p{L}\p{N}]{2,}")

ACTION_WORDS = (
    "claim",
    "airdrop",
    "配布",
    "請求",
    "受け取り",
    "登録",
    "応募",
    "参加",
    "申請",
    "ミント",
    "mint",
    "stake",
    "ステーク",
    "委任",
    "lock",
    "bridge",
    "ブリッジ",
    "farm",
    "deposit",
    "withdraw",
)

TYPE_KEYWORDS: Dict[str, Sequence[str]] = {
    "sale": ("ido", "launch", "sale", "プレセール", "ローンチ"),
    "airdrop": ("airdrop", "エアドロ", "配布", "claim", "クレーム"),
    "mint": ("mint", "ミント"),
    "stake": ("stake", "ステーク", "ステーキング", "委任", "ロック"),
    "kyc": ("kyc", "本人確認"),
    "waitlist": ("waitlist", "登録", "ホワイトリスト", "wl"),
}

RISK_WORDS = ("リスク", "危険", "詐欺", "scam", "rug", "警告", "注意", "落とし穴")
MARKET_WORDS = ("相場", "マーケット", "市場", "地合い", "ブーム", "フロー", "勢い", "トレンド")


@dataclass
class Candidate:
    type: str
    project: str
    action: str
    text: str
    time_wib: str
    time_utc: str
    score: float
    source_idx: int
    tags: List[str]


@dataclass
class MessageSpan:
    source_idx: int
    project: str
    alias: Optional[str]
    original_text: str
    clean_text: str
    time_wib: str
    time_utc: str
    score: float
    tags: List[str]
    kind: str
    tokens: List[str]


@dataclass
class TopicBundle:
    name: str
    messages: List[MessageSpan]
    score: float
    alias: Optional[str] = None


def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = text.replace("\n", " ")
    return " ".join(text.split())


def strip_nonletters(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9一-龥ぁ-んァ-ヶ]", "", text)


def is_url_only(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return True
    without_urls = URL_RE.sub("", stripped)
    return without_urls.strip() == ""


def is_emoji_only(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return True
    without_emoji = EMOJI_RE.sub("", stripped)
    return without_emoji.strip() == ""


def looks_like_media_only(text: str) -> bool:
    stripped = normalize_text(text)
    return bool(MEDIA_ONLY_RE.match(stripped))


def is_noise(text: str) -> bool:
    if not text:
        return True
    normalized = normalize_text(text)
    if len(strip_nonletters(normalized)) <= 2:
        return True
    if FORWARD_RE.match(normalized):
        return True
    if is_url_only(normalized) or is_emoji_only(normalized) or looks_like_media_only(normalized):
        return True
    return False


def _tokenize(text: str) -> List[str]:
    return [token for token in TOKEN_RE.findall(text)]


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _build_alias_lookup(alias_map: Optional[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, str]]:
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


def _resolve_alias(text: str, tokens: Sequence[str], alias_lookup: Dict[str, str]) -> Optional[str]:
    lowered = text.lower()
    for alias, canonical in alias_lookup.items():
        if alias in lowered:
            return canonical
    for token in tokens:
        canonical = alias_lookup.get(token.lower())
        if canonical:
            return canonical
    return None


def _guess_project(tokens: Sequence[str]) -> str:
    for token in tokens:
        if len(token) >= 3 and token.isupper():
            return token
    if tokens:
        return tokens[0]
    return ""


def _compute_score(tags: Sequence[str], project: str, canonical_terms: Dict[str, str]) -> float:
    score = 0.0
    if "actionable" in tags:
        score += 2.0
    if "numeric" in tags:
        score += 2.0
    if "absolute_date" in tags:
        score += 2.0
    if "risk" in tags:
        score += 1.0
    if project and project.lower() in canonical_terms:
        score += 1.0
    return score


def _detect_type(text: str) -> str:
    lowered = text.lower()
    if _contains_any(lowered, RISK_WORDS):
        return "risk"
    if _contains_any(lowered, MARKET_WORDS):
        return "market"
    for typ, keywords in TYPE_KEYWORDS.items():
        if _contains_any(lowered, keywords):
            return typ
    if _contains_any(lowered, ACTION_WORDS):
        return "action"
    return "other"


def collect_spans(messages: List[Dict[str, Any]], alias_map: Optional[Dict[str, Any]] = None) -> List[MessageSpan]:
    alias_lookup, canonical_lookup = _build_alias_lookup(alias_map)
    spans: List[MessageSpan] = []

    for idx, message in enumerate(messages):
        raw_text = (message.get("text") or "").strip()
        if is_noise(raw_text):
            continue

        cleaned = normalize_text(HASHTAG_RE.sub("", raw_text))
        tokens = _tokenize(cleaned)
        if not tokens:
            tokens = _tokenize(raw_text)

        alias = _resolve_alias(cleaned, tokens, alias_lookup)
        project = alias or _guess_project(tokens)

        lowered = cleaned.lower()
        tags: List[str] = []
        if NUMERIC_DETAIL_RE.search(cleaned):
            tags.append("numeric")
        if ABSOLUTE_DATE_RE.search(cleaned):
            tags.append("absolute_date")
        if _contains_any(lowered, ACTION_WORDS):
            tags.append("actionable")
        if _contains_any(lowered, RISK_WORDS):
            tags.append("risk")
        if _contains_any(lowered, MARKET_WORDS):
            tags.append("market")
        if project:
            tags.append("project")

        kind = _detect_type(cleaned)
        score = _compute_score(tags, project, canonical_lookup)

        spans.append(
            MessageSpan(
                source_idx=idx,
                project=project,
                alias=alias,
                original_text=raw_text[:400],
                clean_text=cleaned[:320],
                time_wib=message.get("time_wib", "") or "",
                time_utc=message.get("date", "") or "",
                score=score,
                tags=tags,
                kind=kind,
                tokens=list(tokens),
            )
        )

    return spans


def group_topics(spans: Sequence[MessageSpan]) -> List[TopicBundle]:
    topics: Dict[str, List[MessageSpan]] = {}

    for span in spans:
        if span.project:
            key = span.project
        elif span.tokens:
            key = span.tokens[0]
        else:
            key = f"Topic-{span.source_idx}"
        topics.setdefault(key, []).append(span)

    bundles: List[TopicBundle] = []
    for name, items in topics.items():
        sorted_items = sorted(items, key=lambda s: (-s.score, s.time_utc or "", s.clean_text))
        total_score = sum(s.score for s in sorted_items) + 0.1 * len(sorted_items)
        alias = next((s.alias for s in sorted_items if s.alias), None)
        bundles.append(TopicBundle(name=name, messages=sorted_items, score=total_score, alias=alias))

    bundles.sort(key=lambda b: (-b.score, b.name))
    return bundles


def extract_candidates(messages: List[Dict[str, Any]], alias_map: Optional[Dict[str, Any]] = None) -> List[Candidate]:
    spans = collect_spans(messages, alias_map)
    candidates: List[Candidate] = []

    for span in spans:
        candidate = Candidate(
            type=span.kind,
            project=span.project,
            action="",
            text=span.clean_text[:200],
            time_wib=span.time_wib,
            time_utc=span.time_utc,
            score=span.score,
            source_idx=span.source_idx,
            tags=list(span.tags),
        )
        candidates.append(candidate)

    unique: Dict[Tuple[str, str], Candidate] = {}
    for cand in candidates:
        key = (cand.type, cand.project or cand.text[:60])
        existing = unique.get(key)
        if not existing or cand.score > existing.score:
            unique[key] = cand

    sorted_candidates = sorted(
        unique.values(),
        key=lambda c: (-c.score, c.time_utc or "", c.project, c.text),
    )
    return sorted_candidates
