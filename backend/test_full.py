import subprocess, time, urllib.request, json

# Start server
proc = subprocess.Popen(
    ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
time.sleep(5)

# Login
data = json.dumps({"username": "admin", "password": "admin888"}).encode()
req = urllib.request.Request("http://localhost:8000/api/auth/login", data=data, method="POST")
req.add_header("Content-Type", "application/json")
resp = urllib.request.urlopen(req, timeout=10)
token = json.loads(resp.read()).get("token", "")

# Test 1: Template query
qdata = json.dumps({"question": "总场景数"}).encode()
qreq = urllib.request.Request("http://localhost:8000/api/query", data=qdata, method="POST")
qreq.add_header("Content-Type", "application/json")
qreq.add_header("Authorization", f"Bearer {token}")
try:
    resp = urllib.request.urlopen(qreq, timeout=10)
    r = json.loads(resp.read())
    print(f"[总场景数] OK source={r['query']['source']} elapsed={r['query']['elapsed_ms']}ms")
except Exception as e:
    print(f"[总场景数] FAIL: {e}")

# Test 2: LLM query
qdata2 = json.dumps({"question": "副流感病毒的场景数"}).encode()
qreq2 = urllib.request.Request("http://localhost:8000/api/query", data=qdata2, method="POST")
qreq2.add_header("Content-Type", "application/json")
qreq2.add_header("Authorization", f"Bearer {token}")
try:
    resp = urllib.request.urlopen(qreq2, timeout=60)
    r = json.loads(resp.read())
    if r.get("success"):
        print(f"[副流感病毒] OK source={r['query']['source']} elapsed={r['query']['elapsed_ms']}ms")
        print(f"  SQL: {r['query']['sql'][:150]}")
        rows = r.get("result", {}).get("rows", [])
        if rows:
            print(f"  Result: {rows[:2]}")
    else:
        print(f"[副流感病毒] FAIL: {r.get('error','')[:100]}")
except Exception as e:
    print(f"[副流感病毒] FAIL: {e}")

# Show LLM logs
proc.terminate()
proc.wait(timeout=5)
stderr = proc.stderr.read().decode("utf-8", errors="replace")
for line in stderr.split("\n"):
    if any(kw in line for kw in ["[LLM]", "[路由]", "[意图]", "[IntentLLM]"]):
        print(f"  {line.strip()}")
