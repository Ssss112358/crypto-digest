import json, re
def strip_code_fences(text: str) -> str:
    if not isinstance(text, str): return text
    s = text.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 3:
            body = parts[1]
            return body.split("\n", 1)[-1].strip()
    return s

_SMART = { "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
          "\u2018": "'", "\u2019": "'", "\u2032": "'", "\u2033": '"' }
def _clean(s: str) -> str:
    s = strip_code_fences(s or "")
    for k,v in _SMART.items(): s = s.replace(k, v)
    s = re.sub(r",\s*([\}\]])", r"\1", s)
    return s.strip()

def safe_json_loads(raw: str):
    t = _clean(raw)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict): return obj
    except Exception: pass
    # bracket scan
    stack, start, cands = [], None, []
    for i,ch in enumerate(t):
        if ch == "{":
            if not stack: start = i
            stack.append("{")
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start is not None:
                    cands.append(t[start:i+1]); start = None
    for frag in cands or [t]:
        try:
            obj = json.loads(frag)
            if isinstance(obj, dict): return obj
        except Exception: continue
    raise ValueError("no valid JSON found")