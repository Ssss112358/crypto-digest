DIGEST_PROMPT = r"""
Return exactly one JSON object (starting with { and ending with }). No code fences.

{
  "overall_24h": {
    "summary": string,
    "top_entities": [ "NAME(count)" ],
    "events": [ { "title": string, "when": string, "evidence_ids": [string] } ]
  },
  "delta_recent": {
    "window_hours": %d,
    "new_topics": [ { "title": string, "what_changed": string, "evidence_ids": [string] } ],
    "updates":    [ { "title": string, "what_changed": string, "evidence_ids": [string] } ],
    "deadlines":  [ { "item": string,  "due": string,        "evidence_ids": [string] } ]
  }
}

Guidelines:
- Write every field in natural Japanese. Do not output English sentences.
- Summarise only facts that appear in the logs; never invent or rely on outside knowledge.
- evidence_ids must remain in UTC "YYYY-MM-DD HH:MM:SS" format.
- Each event/topic/deadline must describe the content referenced by the first evidence_ids timestamp.
- If the logs are empty or insufficient, set summary to "\u8a72\u5f53\u30c7\u30fc\u30bf\u306a\u3057" and return empty arrays.
- Output nothing except the JSON object.
--- 24h logs ---
%s
--- recent logs ---
%s
"""
