import subprocess, time, urllib.request, json

proc = subprocess.Popen(
    ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
time.sleep(5)

data = json.dumps({"username": "admin", "password": "admin888"}).encode()
req = urllib.request.Request("http://localhost:8000/api/auth/login", data=data, method="POST")
req.add_header("Content-Type", "application/json")
resp = urllib.request.urlopen(req, timeout=10)
token = json.loads(resp.read()).get("token", "")

q = "甘肃省感冒患者的场景数"
qdata = json.dumps({"question": q}).encode()
qreq = urllib.request.Request("http://localhost:8000/api/query", data=qdata, method="POST")
qreq.add_header("Content-Type", "application/json")
qreq.add_header("Authorization", f"Bearer {token}")

try:
    resp = urllib.request.urlopen(qreq, timeout=120)
    r = json.loads(resp.read())
    if r.get("success"):
        print(f"OK source={r['query']['source']}")
    else:
        print(f"FAIL: {r.get('error','')[:200]}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:500]
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"Error: {e}")

proc.terminate()
proc.wait(timeout=5)
stderr = proc.stderr.read().decode("utf-8", errors="replace")
# Print ALL log lines
for line in stderr.split("\n"):
    if line.strip():
        print(f"  LOG: {line.strip()}")
