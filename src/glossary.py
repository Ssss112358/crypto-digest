from __future__ import annotations
from pathlib import Path
from typing import Any, Dict

import regex as re
import yaml


def load_glossary(path: str = "data/glossary.yml") -> Dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    terms = []
    for item in doc.get("terms", []):
        words = [item.get("key", "")] + list(item.get("synonyms", []) or [])
        words = [w for w in words if w]
        if not words:
            continue
        pattern = re.compile(r"|".join(re.escape(w) for w in words), re.IGNORECASE)
        terms.append({
            "key": item.get("key"),
            "rx": pattern,
            "weight": float(item.get("weight", 1.0)),
        })

    verbs = []
    for name, syns in (doc.get("verbs") or {}).items():
        syn_list = [s for s in syns if s]
        if not syn_list:
            continue
        verbs.append({
            "name": name,
            "rx": re.compile(r"|".join(re.escape(s) for s in syn_list), re.IGNORECASE),
        })

    deny = None
    deny_terms = [d for d in doc.get("deny", []) if d]
    if deny_terms:
        deny = re.compile(r"|".join(re.escape(d) for d in deny_terms), re.IGNORECASE)

    cues = {}
    for cue_key, cue_list in (doc.get("cue_phrases") or {}).items():
        tokens = [c for c in cue_list if c]
        if not tokens:
            continue
        cues[cue_key] = re.compile(r"|".join(re.escape(token) for token in tokens), re.IGNORECASE)

    return {"terms": terms, "verbs": verbs, "deny": deny, "cues": cues}
