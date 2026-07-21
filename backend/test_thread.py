import threading, time, urllib.request, json

# Start uvicorn in a thread
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
print(f"Login OK")

# LLM query
q = "甘肃省感冒患者的场景数"
print(f"[{q}] waiting for LLM response (up to 120s)...")
qdata = json.dumps({"question": q}).encode()
qreq = urllib.request.Request("http://localhost:8000/api/query", data=qdata, method="POST")
qreq.add_header("Content-Type", "application/json")
qreq.add_header("Authorization", f"Bearer {token}")

try:
    resp = urllib.request.urlopen(qreq, timeout=120)
    r = json.loads(resp.read())
    if r.get("success"):
        print(f"OK source={r['query']['source']} elasped={r['query']['elapsed_ms']}ms")
        print(f"SQL: {r['query']['sql']}")
    else:
        print(f"FAIL: {r.get('error','')[:200]}")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")[:500]
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
