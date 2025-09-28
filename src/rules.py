from __future__ import annotations

import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ALIAS_PATH = Path(__file__).resolve().parents[1] / "data" / "aliases.yml"

_EMERGENCY_PATTERN = re.compile(r"(?i)\b(hack|exploit|rug|scam|重大|障害|停止|不正|freeze|halt|attack)\b")
_MARKET_PATTERN = re.compile(r"(?i)(regulation|上場|listing|listing|funding|資金調達|提携|partnership|acquire|投資|news|update|発表|承認|approval|ローンチ|launch)")
_TRADING_PATTERN = re.compile(r"(?i)\b(long|short|entry|exit|buy|sell|tp|sl|stop[- ]?loss|利確|損切|成行|指値|約定)\b")
_SALES_PATTERN = re.compile(r"(?i)(ieo|ido|ico|presale|プレセール|トークンセール|ローンチパッド|launchpad|whitelist|ホワイトリスト|sale)")
_AIRDROP_PATTERN = re.compile(r"(?i)(airdrop|claim|ポイント|reward|rewards|quest|task|ミッション|キャンペーン|応募|抽選|ポイント|rewards)")
_DEADLINE_PATTERN = re.compile(r"(?i)(締切|deadline|〆切|KYC|申請|提出|スナップショット|snapshot|claim期限)")
_DEADLINE_TIME_PATTERN = re.compile(r"(?P<date>(\d{4}-\d{2}-\d{2})|(\d{1,2}/\d{1,2}))(?:[^0-9]{0,3}(?P<time>\d{1,2}:\d{2}))?")
_TECH_PATTERN = re.compile(r"(?i)(upgrade|アップデート|メンテ|maintenance|deploy|patch|bug|fix|testnet|beta|release|ローンチ|修正)")
_RESOURCE_PATTERN = re.compile(r"(?i)(docs?|documentation|guide|thread|スレ|公式|詳細|こちら|詳しくはこちら)")
_URL_PATTERN = re.compile(r"https?://\S+")
_TOKEN_PATTERN = re.compile(r"\b[A-Z0-9]{3,6}\b")

CATEGORY_KEYS = {
    "emergency": "emergency",
    "market_news": "market_news",
    "trading": "trading",
    "sales": "sales",
    "airdrops": "airdrops",
    "deadlines": "deadlines",
    "tech_updates": "tech_updates",
    "resources": "resources",
}


def _load_aliases() -> Dict[str, Any]:
    if ALIAS_PATH.exists():
        try:
            data = yaml.safe_load(ALIAS_PATH.read_text(encoding="utf-8")) or {}
            return {
                "tokens": {k.upper(): v.upper() for k, v in data.get("tokens", {}).items()},
                "chains": {str(item).upper() for item in data.get("chains", [])},
                "projects": {k.upper(): v.upper() for k, v in data.get("projects", {}).items()},
                "keywords": {k: [re.compile(r"(?i)\\b" + kw + r"\\b") for kw in v] for k, v in data.get("keywords", {}).items()},
            }
        except Exception:
            return {"tokens": {}, "chains": set(), "projects": {}, "keywords": {}}
    return {"tokens": {}, "chains": set(), "projects": {}, "keywords": {}}


_ALIASES_DATA = _load_aliases()
_TOKEN_ALIASES = _ALIASES_DATA["tokens"]
_CHAIN_NAMES = _ALIASES_DATA["chains"]
_PROJECT_ALIASES = _ALIASES_DATA["projects"]
_KEYWORD_PATTERNS = _ALIASES_DATA["keywords"]


def _normalize_topic(token: str) -> str:
    up = token.upper()
    if up in _TOKEN_ALIASES:
        return _TOKEN_ALIASES[up]
    if up in _PROJECT_ALIASES:
        return _PROJECT_ALIASES[up]
    return up


def _extract_topics(text: str) -> Set[str]:
    topics: Set[str] = set()
    for match in _TOKEN_PATTERN.findall(text):
        if match.upper() in {"HTTP", "HTTPS"}:
            continue
        if len(match) <= 2:
            continue
        topics.add(_normalize_topic(match))

    for project_alias in _PROJECT_ALIASES.keys():
        if project_alias.lower() in text.lower():
            topics.add(_PROJECT_ALIASES[project_alias])

    for chain in _CHAIN_NAMES:
        if chain.lower() in text.lower():
            topics.add(chain)
    return topics


def _extract_deadline(text: str) -> Optional[str]:
    m = _DEADLINE_TIME_PATTERN.search(text)
    if not m:
        return None
    date = m.group("date")
    time = m.group("time") or "00:00"
    if "/" in date:
        parts = date.split("/")
        month, day = parts[0].zfill(2), parts[1].zfill(2)
        year = datetime.utcnow().year
        date = f"{year}-{month}-{day}"
    return f"{date} {time}"


def tag_message(message: Dict[str, Any]) -> Dict[str, Any]:
    text = message.get("text", "") or ""
    lower = text.lower()
    categories: Set[str] = set()
    action_tags: Set[str] = set()

    if _EMERGENCY_PATTERN.search(text):
        categories.add("emergency")
    if _MARKET_PATTERN.search(text):
        categories.add("market_news")
    if _TRADING_PATTERN.search(text):
        categories.add("trading")
    if _SALES_PATTERN.search(text):
        categories.add("sales")
    if _AIRDROP_PATTERN.search(text):
        categories.add("airdrops")
    if _DEADLINE_PATTERN.search(text) or _DEADLINE_TIME_PATTERN.search(text):
        categories.add("deadlines")
    if _TECH_PATTERN.search(text):
        categories.add("tech_updates")
    if _RESOURCE_PATTERN.search(text) or _URL_PATTERN.search(text):
        categories.add("resources")

    for tag_name, patterns in _KEYWORD_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(text):
                action_tags.add(tag_name)
                if tag_name == "entry" or tag_name == "sl" or tag_name == "tp":
                    categories.add("trading")
                elif tag_name == "sale":
                    categories.add("sales")
                elif tag_name == "airdrop":
                    categories.add("airdrops")
                elif tag_name == "deadline":
                    categories.add("deadlines")
                break

    topics = _extract_topics(text)
    deadline = _extract_deadline(text)

    return {
        "categories": sorted(categories),
        "topics": sorted(topics),
        "deadline": deadline,
        "action_tags": sorted(action_tags),
    }
