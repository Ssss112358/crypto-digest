import requests

def _chunks(s: str, n: int):
    return [s[i:i+n] for i in range(0, len(s), n)]

def post_markdown(webhook_url: str, markdown: str):
    for part in _chunks(markdown, 1800):
        response = requests.post(webhook_url, json={"content": part}, timeout=30)
        if response.status_code >= 300:
            raise RuntimeError(f"discord webhook {response.status_code}: {response.text[:200]}")
