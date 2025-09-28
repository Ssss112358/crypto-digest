DIGEST_PROMPT = r"""
出力は必ず以下のJSON一個のみ（コードフェンス禁止）。
{
  "overall_24h": {
    "summary": "短い総括（日本語）",
    "top_entities": ["XPL(31)","ASTER(22)"],
    "speakers": [{"name":"rairai132","count":16}],
    "highlights": [
      {"title":"要点1","evidence_ids":["2025-09-27 06:46:26","2025-09-27 09:58:00"]}
    ]
  },
  "by_category": {
    "emergency":        [{"title":"～","what":"～","evidence_ids":[...] }],
    "market_news":      [{"title":"～","what":"～","evidence_ids":[...] }],
    "trading":          [{"pair":"BTC/USDT","signal":"long/short/entry/exit/TP/SL","what":"～","evidence_ids":[...] }],
    "sales":            [{"project":"～","venue":"OKX/Bybit/LPAD等","when":"YYYY-MM-DD HH:MM","what":"～","evidence_ids":[...] }],
    "airdrops":         [{"project":"～","action":"claim/task/stake等","what":"～","evidence_ids":[...] }],
    "deadlines":        [{"item":"～","due":"YYYY-MM-DD HH:MM","tz":"UTC","evidence_ids":[...] }],
    "tech_updates":     [{"project":"～","what":"～","evidence_ids":[...] }],
    "resources":        [{"title":"～","url":"～","evidence_ids":[...] }],
    "other_topics":     ["tokenA","projectB"]
  },
  "recent_delta": {
    "window_hours": %d,
    "new_topics": [{"title":"～","what_changed":"～","evidence_ids":[...] }],
    "updates":    [{"title":"～","what_changed":"～","evidence_ids":[...] }],
    "resolved":   [{"title":"～","reason":"～","evidence_ids":[...] }]
  }
}

要件:
- 全フィールドを自然な日本語で記述し、固有名詞以外に英語文を混在させない。
- 24h logs / recent logs に記載された事実のみを要約し、外部知識や推測は一切書かない。
- evidence_ids は必ず UTC "YYYY-MM-DD HH:MM:SS" 形式で最大3件まで列挙する。
- "summary" や各カテゴリが空の場合は "該当データなし" を設定し、配列は空リストにする。
- "other_topics" は重複を除外し、アルファベット順に整列したプロジェクト/トークン名をすべて含める。
- 各カテゴリ要素の "what" や "signal" などは簡潔な日本語で記述し、`TAGS:` に付与された候補を参考に分類する。
- trading/sales/airdrops/deadlines は対象、場所/アクション、日時が分かるように記述する。
- "speakers" はメッセージ数上位の発言者を多い順に3～5名、"top_entities" は頻出トークン名を最多順に並べる。
- recent_delta.resolved には今回取り下げ・解消されたトピックと理由を入れる。
- 特定カテゴリに該当する内容が無い場合、そのカテゴリは空配列のまま返す。

補助情報:
- ログは1件ずつ "TAGS:" 行に候補カテゴリ・トピック・締切が付与されているので分類に必ず利用する。
- 締切日時が複数書かれている場合でも最重要なものを1つ選んで記載する。

--- 24h logs ---
%s
--- recent logs ---
%s
"""
