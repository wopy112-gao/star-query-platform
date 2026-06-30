"""星宝语料场景查询系统 — FastAPI 主入口"""

import uvicorn
from pathlib import Path
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from sql_engine import engine
from auth import get_current_user

# ---- 静态文件目录 ----
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 应用
app = FastAPI(
    title="星宝语料场景查询系统",
    description="基于自然语言的星宝语料场景数据查询与可视化系统",
    version="3.2.0",
)

# CORS（开发时允许前端 5173 端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- 启动事件 ----
@app.on_event("startup")
async def startup():
    """启动时加载数据"""
    try:
        info = engine.load_data()
        print(f"[启动] 数据加载完成: {info['total_rows']} 行 × {info['total_cols']} 列")
    except Exception as e:
        print(f"[启动] 数据加载失败: {e}")
        print("[启动] 系统仍可启动，但查询功能不可用")


# ---- 路由注册 ----
from auth_router import router as auth_router
from query_router import router as query_router
from admin_router import router as admin_router
from data_export_router import router as data_export_router

app.include_router(auth_router)
app.include_router(query_router)
app.include_router(admin_router)
app.include_router(data_export_router)


# ---- 健康检查 ----
@app.get("/api/health")
def health():
    """健康检查"""
    info = engine.get_schema()
    return {
        "status": "ok",
        "data_loaded": engine.is_loaded,
        "total_rows": engine.row_count,
        "mapping": info.get("mapping"),
    }


# ---- 映射表热加载（无需重启） ----
@app.post("/api/admin/reload-mapping")
def reload_mapping(username: str = Depends(get_current_user)):
    """热加载药品 ATC 映射表
    
    增量映射合并到映射表文件后，调用此接口
    即可生效，无需重启服务。
    """
    info = engine.load_mapping_table()
    return {
        "success": info.get("loaded", False),
        "mapping": info,
    }


# ---- 静态文件服务（生产模式） ----
if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    # 挂载静态文件
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc: Exception):
        """SPA 路由回退：非 /api/ 路径统一返回 index.html"""
        if not request.url.path.startswith("/api/"):
            from fastapi.responses import FileResponse
            from starlette.responses import Response
            
            filepath = STATIC_DIR / "index.html"
            if filepath.exists():
                content = filepath.read_bytes()
                return Response(
                    content=content,
                    media_type="text/html",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                    }
                )
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    print(f"[静态文件] 已挂载前端资源: {STATIC_DIR}")


# ---- 全局异常处理 ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": f"服务器内部错误: {str(exc)}"},
    )


# ---- 入口 ----
if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
