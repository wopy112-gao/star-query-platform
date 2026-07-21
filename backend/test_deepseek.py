import urllib.request, json, time

url = "https://api.deepseek.com/v1/chat/completions"

messages = [
    {"role": "system", "content": "你是医药数据SQL专家。根据问题生成DuckDB SQL。数据表data包含字段：场景ID, 疾病名称, 省份, 城市。"},
    {"role": "user", "content": "甘肃省感冒患者的场景数"}
]

payload = json.dumps({
    "model": "deepseek-chat",
    "messages": messages,
    "temperature": 0.1,
    "max_tokens": 500,
}).encode()

print(f"Testing DeepSeek API...")
start = time.time()

req = urllib.request.Request(url, data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-c54045b6059544b68425bc08e82ad404",
    }, method="POST")

try:
    resp = urllib.request.urlopen(req, timeout=30)
    elapsed = time.time() - start
    body = json.loads(resp.read())
    print(f"OK ({elapsed:.1f}s)")
    print(f"Response: {body['choices'][0]['message']['content'][:200]}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:300]
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    elapsed = time.time() - start
    print(f"Error after {elapsed:.1f}s: {type(e).__name__}: {e}")
