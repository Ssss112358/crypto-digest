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


def _load_aliases() -> Dict[str, str]:
    if ALIAS_PATH.exists():
        try:
            data = yaml.safe_load(ALIAS_PATH.read_text(encoding="utf-8")) or {}
            aliases = data.get("aliases", {})
            return {k.upper(): v.upper() for k, v in aliases.items()}
        except Exception:
            return {}
    return {}


def _load_chain_names() -> Set[str]:
    if ALIAS_PATH.exists():
        try:
            data = yaml.safe_load(ALIAS_PATH.read_text(encoding="utf-8")) or {}
            return {str(item) for item in data.get("chains", [])}
        except Exception:
            return set()
    return set()

_ALIASES = _load_aliases()
_CHAIN_NAMES = _load_chain_names()


def _normalize_topic(token: str) -> str:
    up = token.upper()
    return _ALIASES.get(up, up)


def _extract_topics(text: str) -> Set[str]:
    topics: Set[str] = set()
    for match in _TOKEN_PATTERN.findall(text):
        if match.upper() in {"HTTP", "HTTPS"}:
            continue
        if len(match) <= 2:
            continue
        norm = _normalize_topic(match)
        topics.add(norm)
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

    topics = _extract_topics(text)
    deadline = _extract_deadline(text)

    return {
        "categories": sorted(categories),
        "topics": sorted(topics),
        "deadline": deadline,
    }
