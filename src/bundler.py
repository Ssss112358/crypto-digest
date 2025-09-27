from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Dict, Any

def bundle_conversations(msgs: List[Dict[str, Any]], window_min: int = 8) -> List[List[Dict[str, Any]]]:
    """同一チャット内で時刻差が短い発言を束ねて文脈を付与。"""
    def _dt(s: str) -> datetime:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    bundles: List[List[Dict[str, Any]]] = []
    msgs_sorted = sorted(msgs, key=lambda x: x["date"])
    current: List[Dict[str, Any]] = []
    last_dt = None
    last_chat = None
    for m in msgs_sorted:
        dt = _dt(m["date"])
        if not current:
            current = [m]; last_dt = dt; last_chat = m["chat"]; continue
        gap = (dt - last_dt).total_seconds()/60.0
        if gap <= window_min and m["chat"] == last_chat:
            current.append(m)
        else:
            bundles.append(current)
            current = [m]
        last_dt = dt; last_chat = m["chat"]
    if current: bundles.append(current)
    return bundles

def bundles_to_text(bundles: List[List[Dict[str, Any]]]) -> str:
    lines = []
    for conv in bundles:
        lines.append("---")
        for m in conv:
            lines.append(f'{m["date"]} {m["chat"]} {m["from"]}: {m["text"].replace(chr(10)," ")}')
    return "\n".join(lines)