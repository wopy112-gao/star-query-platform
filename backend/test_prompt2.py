import urllib.request, json, time

url = "https://apihub.agnes-ai.com/v1/chat/completions"

# Simulate a realistic prompt (simpler version)
messages = [
    {"role": "system", "content": "你是医药数据SQL专家。根据问题生成DuckDB SQL。数据表data包含字段：场景ID, 疾病名称, 省份, 城市。场景数=COUNT(DISTINCT 场景ID)。"},
    {"role": "user", "content": "甘肃省感冒患者的场景数"}
]

payload = json.dumps({
    "model": "agnes-2.0-flash",
    "messages": messages,
    "temperature": 0.1,
    "max_tokens": 500,
}).encode()

print(f"Payload size: {len(payload)} bytes")
start = time.time()

req = urllib.request.Request(url, data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-ZBh9mpQac4lgE7d9nnPzfTDB2l4zovPMxcu7zDQXbw0P6hYb",
    }, method="POST")

try:
    resp = urllib.request.urlopen(req, timeout=60)
    elapsed = time.time() - start
    body = json.loads(resp.read())
    print(f"OK ({elapsed:.1f}s): {body['choices'][0]['message']['content'][:200]}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:500]
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    elapsed = time.time() - start
    print(f"Error after {elapsed:.1f}s: {type(e).__name__}: {e}")
