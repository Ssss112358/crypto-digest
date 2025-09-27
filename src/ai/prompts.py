DIGEST_PROMPT = r"""
最終出力はJSON一個のみ（先頭{〜末尾}）。コードフェンス禁止。

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

要件:
- evidence_ids は UTC "YYYY-MM-DD HH:MM:SS"
- 抽象語を避け、銘柄/機能/取引所/操作など具体語を用いる
- JSON以外の文字を出さない
--- 24h logs ---
%s
--- recent logs ---
%s
"""