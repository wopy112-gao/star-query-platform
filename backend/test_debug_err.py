import sys; sys.path.insert(0, ".")

# Patch app to log exceptions
import app as app_module
from fastapi.responses import JSONResponse
import traceback

original_handler = app_module.app.exception_handler(Exception)

@app_module.app.exception_handler(Exception)
async def debug_exception_handler(request, exc):
    print(f"[GLOBAL ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"success": False, "error": str(exc)[:200]})

from fastapi.testclient import TestClient
client = TestClient(app_module.app)

# Login
resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin888"})
token = resp.json().get("token", "")
headers = {"Authorization": f"Bearer {token}"}

# Complex query
resp = client.post("/api/query", json={"question": "甘肃省感冒患者的场景数"}, headers=headers, timeout=60)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
