import urllib.request, json

# 1. Login test
data = json.dumps({"username": "admin", "password": "admin888"}).encode()
req = urllib.request.Request("http://localhost:8000/api/auth/login", data=data, method="POST")
req.add_header("Content-Type", "application/json")

resp = urllib.request.urlopen(req, timeout=10)
result = json.loads(resp.read())
token = result.get("token", "")
print(f"Login: SUCCESS (status={resp.status})")
print(f"Token: {token[:60]}...")

# 2. Query test with token
query_data = json.dumps({"question": "总场景数"}).encode()
qreq = urllib.request.Request("http://localhost:8000/api/query", data=query_data, method="POST")
qreq.add_header("Content-Type", "application/json")
qreq.add_header("Authorization", f"Bearer {token}")

qresp = urllib.request.urlopen(qreq, timeout=30)
qresult = json.loads(qresp.read())
print(f"\nQuery: SUCCESS (status={qresp.status})")
print(f"Question: 总场景数")
print(f"Answer: {json.dumps(qresult, ensure_ascii=False)[:500]}")
