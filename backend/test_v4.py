import threading, time, urllib.request, json
import uvicorn
from app import app

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

t = threading.Thread(target=run_server, daemon=True)
t.start()
time.sleep(5)

# Login
data = json.dumps({"username": "admin", "password": "admin888"}).encode()
req = urllib.request.Request("http://localhost:8000/api/auth/login", data=data, method="POST")
req.add_header("Content-Type", "application/json")
resp = urllib.request.urlopen(req, timeout=10)
token = json.loads(resp.read()).get("token", "")

# Test all query types
tests = [
    "总场景数",
    "甘肃省感冒患者的场景数",
    "阿莫西林的销售场景数",
]

for q in tests:
    qdata = json.dumps({"question": q}).encode()
    qreq = urllib.request.Request("http://localhost:8000/api/query", data=qdata, method="POST")
    qreq.add_header("Content-Type", "application/json")
    qreq.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(qreq, timeout=30)
        r = json.loads(resp.read())
        src = r["query"]["source"]
        ms = r["query"]["elapsed_ms"]
        ok = "✅" if r.get("success") else "❌"
        print(f"{ok} [{q}] source={src} {ms}ms")
        if r.get("success"):
            print(f"   SQL: {r['query']['sql'][:150]}")
    except Exception as e:
        print(f"❌ [{q}] {type(e).__name__}")
