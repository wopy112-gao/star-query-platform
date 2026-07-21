import urllib.request, json

# Simulate the exact call llm_translator would make
url = "https://apihub.agnes-ai.com/v1/chat/completions"

messages = [
    {"role": "system", "content": "You are a SQL expert. Generate DuckDB SQL."},
    {"role": "user", "content": "甘肃省感冒患者的场景数"}
]

payload = json.dumps({
    "model": "agnes-2.0-flash",
    "messages": messages,
    "temperature": 0.1,
    "max_tokens": 500,
}).encode()

req = urllib.request.Request(url, data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-ZBh9mpQac4lgE7d9nnPzfTDB2l4zovPMxcu7zDQXbw0P6hYb",
    }, method="POST")

try:
    resp = urllib.request.urlopen(req, timeout=30)
    body = json.loads(resp.read())
    print(f"OK: {resp.status}")
    print(f"Response: {body['choices'][0]['message']['content'][:200]}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:500]
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
