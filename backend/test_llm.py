import subprocess, time, urllib.request, json

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

# Test query that needs LLM (not in templates)
qdata = json.dumps({"question": "副流感病毒的场景数"}).encode()
qreq = urllib.request.Request("http://localhost:8000/api/query", data=qdata, method="POST")
qreq.add_header("Content-Type", "application/json")
qreq.add_header("Authorization", f"Bearer {token}")

try:
    qresp = urllib.request.urlopen(qreq, timeout=60)
    result = json.loads(qresp.read())
    if result.get("success"):
        q = result.get("query", {})
        print(f"Query: SUCCESS")
        print(f"SQL: {q.get('sql', '')[:200]}")
        print(f"Source: {q.get('source', '')}")
        print(f"Elapsed: {q.get('elapsed_ms', '')}ms")
        r = result.get("result", {})
        print(f"Rows: {r.get('rows', [{}])[0]}")
    else:
        print(f"Query FAILED: {result.get('error', '')}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    try:
        print(f"Body: {e.read().decode()[:500]}")
    except:
        pass
except Exception as e:
    print(f"Error: {e}")

# Get server stderr
time.sleep(2)
proc.terminate()
proc.wait(timeout=5)
stderr = proc.stderr.read().decode("utf-8", errors="replace")
for line in stderr.split("\n"):
    if any(kw in line for kw in ["[LLM]", "[路由]", "[意图]", "[恢复]", "[IntentLLM]"]):
        print(f"  {line.strip()}")
