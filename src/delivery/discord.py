import requests

def _chunks(s: str, n: int):
    return [s[i:i+n] for i in range(0, len(s), n)]

def post_markdown(webhook_url: str, markdown: str):
    for part in _chunks(markdown, 1800):
        r = requests.post(webhook_url, json={"content": part})
        if r.status_code >= 300:
            # ここで本文はログに出さない
            print(f"[warn] discord status={r.status_code}")
