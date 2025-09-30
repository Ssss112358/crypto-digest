from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import regex as re


LINK_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"#[\w\-]+")
EMOJI_RE = re.compile(r"\p{Emoji_Presentation}|\p{Emoji}\uFE0F?", re.UNICODE)
MEDIA_ONLY_RE = re.compile(r"^\[(?:photo|image|video|voice|sticker|animation)\]$", re.IGNORECASE)
FORWARD_RE = re.compile(r"^(?:Forwarded from|転送元:?|Forwarded message)$", re.IGNORECASE)
ABSOLUTE_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
RELATIVE_ONLY_RE = re.compile(r"^(?:soon|later|まもなく|すぐ|程なく|直後|今夜|後で|今すぐ)\b", re.IGNORECASE)
NUMERIC_DETAIL_RE = re.compile(r"\b\d+(?:\.\d+)?(?:%|x|倍|枚|人|pt|ポイント|usd|usdt|eth|sol|btc|k|m|b)?\b", re.IGNORECASE)
UPPER_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9\-]{1,10}\b")

TYPE_KEYWORDS: Dict[str, Sequence[str]] = {
    "sale": ("ido", "launch", "sale", "プレセール", "ラウンチ", "トークンセール"),
    "airdrop": ("airdrop", "エアドロ", "配布", "claim", "クレーム", "ドロップ"),
    "mint": ("mint", "ミント", "铸造"),
    "stake": ("stake", "ステーク", "ステーキング", "委任", "ロック"),
    "kyc": ("kyc", "本人確認"),
    "waitlist": ("waitlist", "登録", "ホワイトリスト", "wl"),
}
ACTION_WORDS = (
    "claim",
    "register",
    "stake",
    "mint",
    "apply",
    "submit",
    "join",
    "swap",
    "bridge",
    "farm",
    "deposit",
    "claim",
    "ハント",
    "登録",
    "応募",
    "申請",
    "参加",
    "受け取り",
    "請求",
    "ステーク",
    "ミント",
    "ブリッジ",
    "フォロー",
    "KYC",
)
RISK_WORDS = ("リスク", "危険", "詐欺", "scam", "運営売り", "高FDV", "薄い板", "rug", "警告", "注意")
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


def _strip_noise(text: str) -> str:
    without_links = LINK_RE.sub(" ", text)
    without_hash = HASHTAG_RE.sub(" ", without_links)
    without_emoji = EMOJI_RE.sub("", without_hash)
    normalized = " ".join(without_emoji.split())
    return normalized.strip()


def _is_link_or_emoji_only(text: str) -> bool:
    no_links = LINK_RE.sub(" ", text)
    stripped = EMOJI_RE.sub("", no_links)
    stripped = stripped.strip()
    if not stripped:
        return True
    if MEDIA_ONLY_RE.match(text.strip()):
        return True
    return False


def _guess_project(text: str) -> str:
    tokens = UPPER_TOKEN_RE.findall(text)
    for token in tokens:
        if len(token) >= 3 and not token.isdigit():
            return token
    return ""


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _detect_type(text: str) -> str:
    lowered = text.lower()
    risk_hit = _contains_any(lowered, RISK_WORDS)
    if risk_hit:
        return "risk"
    for typ in ("sale", "airdrop", "mint", "stake", "kyc", "waitlist"):
        words = TYPE_KEYWORDS.get(typ, [])
        if _contains_any(lowered, words):
            return typ
    if _contains_any(lowered, MARKET_WORDS):
        return "market"
    return "other"


def _collect_alias_terms(alias_map: Optional[Dict[str, Any]]) -> Set[str]:
    terms: Set[str] = set()
    if not isinstance(alias_map, dict):
        return terms
    for value in (alias_map.get("aliases") or {}).values():
        if isinstance(value, str):
            terms.add(value.lower())
    chains = alias_map.get("chains") or []
    for chain in chains:
        if isinstance(chain, str):
            terms.add(chain.lower())
    return terms


def extract_candidates(messages: List[Dict[str, Any]], alias_map: Optional[Dict[str, Any]] = None) -> List[Candidate]:
    alias_terms = _collect_alias_terms(alias_map)
    results: List[Candidate] = []

    for idx, message in enumerate(messages):
        raw_text = (message.get("text") or "").strip()
        if not raw_text:
            continue
        if FORWARD_RE.match(raw_text):
            continue
        if _is_link_or_emoji_only(raw_text):
            continue

        stripped = _strip_noise(raw_text)
        if len(stripped) < 8:
            continue
        if RELATIVE_ONLY_RE.match(stripped.lower()):
            continue

        lowered = stripped.lower()
        candidate_type = _detect_type(stripped)

        has_action = _contains_any(lowered, ACTION_WORDS)
        has_numeric = bool(NUMERIC_DETAIL_RE.search(stripped))
        has_abs_date = bool(ABSOLUTE_DATE_RE.search(stripped))
        project = _guess_project(stripped)

        tags: List[str] = []
        if has_abs_date:
            tags.append("absolute_date")
        if candidate_type == "market":
            tags.append("market_pulse")
        if has_action:
            tags.append("actionable")
        if has_numeric:
            tags.append("numeric")

        score = 0.0
        if has_action:
            score += 2.0
        if has_numeric or has_abs_date:
            score += 2.0
        if candidate_type == "risk" and _contains_any(lowered, RISK_WORDS):
            score += 1.0
        if project and project.lower() in alias_terms:
            score += 1.0
        else:
            tokens = [tok.lower() for tok in stripped.split()]
            if alias_terms and any(tok in alias_terms for tok in tokens):
                score += 1.0

        candidate = Candidate(
            type=candidate_type,
            project=project,
            action="",
            text=stripped[:200],
            time_wib=message.get("time_wib", "") or "",
            time_utc=message.get("date", "") or "",
            score=score,
            source_idx=idx,
            tags=tags,
        )
        results.append(candidate)

    dedup: Dict[tuple, Candidate] = {}
    for cand in results:
        key = (cand.type, cand.project or cand.text[:40])
        existing = dedup.get(key)
        if not existing or cand.score > existing.score:
            dedup[key] = cand

    sorted_candidates = sorted(
        dedup.values(),
        key=lambda c: (-c.score, c.time_utc or "", c.project, c.text),
    )
    return sorted_candidates
