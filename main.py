import uvicorn
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth import auth_manager
from proxy import proxy_client
from token_tracker import TokenTracker
from quota_manager import quota_manager
from anthropic_proxy import anthropic_proxy_client

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = FastAPI(
    title="LLM Gateway",
    description="统一大模型网关",
    version="2.0.0",
)


class ChatMessage(BaseModel):
    role: str
    content: Any
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    model_config = {"extra": "allow"}


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Gateway-Version"] = "2.0.0"
    return response


# ============ 认证工具函数 ============

def extract_api_key(request: Request) -> Optional[str]:
    """从请求中提取 API Key"""
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    
    # 调试日志
    logger.info(f"DEBUG - Full headers: {dict(request.headers)}")
    logger.info(f"DEBUG - Authorization header: {repr(auth_header)}")
    
    if auth_header:
        parts = auth_header.split()
        logger.info(f"DEBUG - Authorization parts: {parts}")
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1] if parts[1] else None
        elif len(parts) == 1:
            return parts[0] if parts[0] else None
        elif len(parts) > 2:
            # 处理 Bearer token 中间有空格的情况
            return parts[-1] if parts[-1] else None
    
    api_key = request.query_params.get("api_key")
    logger.info(f"DEBUG - Query api_key: {repr(api_key)}")
    
    return None


def verify_required(request: Request) -> str:
    """强制认证"""
    key = extract_api_key(request)
    if not key:
        raise JSONResponse(status_code=401, content={"detail": "Missing Authorization header or api_key query parameter"})
    if key not in auth_manager._keys:
        raise JSONResponse(status_code=401, content={"detail": "Invalid API Key"})
    return key


# ============ 核心对话接口（强制认证） ============

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    api_key = verify_required(request)
    x_reasoning = request.headers.get("x-reasoning")
    
    result = await proxy_client.chat_completions(
        api_key=api_key,
        body=body,
        reasoning=x_reasoning,
    )
    
    if isinstance(result, dict):
        return JSONResponse(content=result)
    return result


# ============ 模型发现接口（完全公开，可选认证） ============

@app.get("/v1/models")
@app.get("/models")
async def list_models(request: Request):
    """完全公开，无需认证"""
    key = extract_api_key(request)
    logger.info(f"DEBUG - Extracted key: {repr(key)}")
    
    # 如果提供了有效 key，记录但不强制
    if key and key not in auth_manager._keys:
        logger.warning(f"DEBUG - Invalid key provided: {repr(key)}")
        return JSONResponse(status_code=401, content={"detail": "Invalid API Key"})

    from config_loader import load_config
    config = load_config()
    
    models = []
    for provider in config.gateway.providers:
        for alias, model_cfg in provider.models.items():
            if hasattr(model_cfg, "real_name"):
                real_name = model_cfg.real_name
                daily_quota = getattr(model_cfg, "daily_quota_tokens", 0)
                monthly_quota = getattr(model_cfg, "monthly_quota_tokens", 0)
                fallback = list(getattr(model_cfg, "fallback_models", []))
            else:
                real_name = str(model_cfg)
                daily_quota = 0
                monthly_quota = 0
                fallback = []
            
            state = quota_manager.get_state(alias)
            models.append({
                "id": alias,
                "object": "model",
                "owned_by": provider.name,
                "real_model": real_name,
                "quota": {
                    "daily": daily_quota,
                    "monthly": monthly_quota,
                },
                "fallback_models": fallback,
                "current_usage": {
                    "daily_tokens": state.daily_used_tokens if state else 0,
                    "daily_rate": round(state.daily_usage_rate * 100, 2) if state else 0,
                    "monthly_tokens": state.monthly_used_tokens if state else 0,
                    "monthly_rate": round(state.monthly_usage_rate * 100, 2) if state else 0,
                    "near_limit": state.is_near_limit if state else False,
                    "exceeded": state.is_exceeded if state else False,
                }
            })
    
    return {"object": "list", "data": models}


@app.get("/api/v1/models")
async def list_models_compat_v1(request: Request):
    return await list_models(request)


@app.get("/api/tags")
async def ollama_tags(request: Request):
    models_data = await list_models(request)
    return {
        "models": [
            {
                "name": m["id"],
                "model": m["id"],
                "modified_at": "2024-01-01T00:00:00Z",
                "size": 0,
                "digest": "",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": m["owned_by"],
                    "families": [m["owned_by"]],
                    "parameter_size": "",
                    "quantization_level": "",
                }
            }
            for m in models_data["data"]
        ]
    }


@app.get("/v1/props")
@app.get("/props")
async def props_compat(request: Request):
    return {}


@app.get("/version")
async def version_compat():
    return {"version": "0.3.0"}


# ============ Token 统计接口 ============

@app.get("/v1/usage")
async def get_usage_by_api_key(request: Request):
    key = request.query_params.get("key")
    if not key:
        return JSONResponse(status_code=400, content={"detail": "Missing key query parameter"})
    
    if key not in auth_manager._keys:
        return JSONResponse(status_code=401, content={"detail": "Invalid API Key"})
    
    tracker = TokenTracker()
    stats = tracker.get_stats(key)
    
    if not stats:
        key_info = auth_manager.get_key_info(key)
        return {
            "status": "success",
            "data": {
                "api_key": key,
                "name": key_info.name if key_info else "",
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "requests": 0,
                "daily": {},
                "models": {},
            }
        }
    
    return {
        "status": "success",
        "data": {
            "api_key": key,
            "name": stats.get("name", ""),
            "total_prompt_tokens": stats.get("total_prompt_tokens", 0),
            "total_completion_tokens": stats.get("total_completion_tokens", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "requests": stats.get("requests", 0),
            "daily": stats.get("daily", {}),
            "models": stats.get("models", {}),
        }
    }


@app.get("/v1/quota")
async def get_quota_report(request: Request):
    api_key = verify_required(request)
    return {
        "status": "success",
        "data": quota_manager.get_quota_report(),
    }


@app.get("/v1/stats")
async def get_stats(request: Request):
    api_key = verify_required(request)
    target_key = request.query_params.get("target_key")
    
    tracker = TokenTracker()
    
    if target_key:
        stats = tracker.get_stats(target_key)
    else:
        stats = tracker.get_stats()
    
    return {
        "status": "success",
        "data": stats,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "gateway": "running",
        "features": ["multi-provider", "model-mapping", "token-tracking", 
                     "streaming", "reasoning-switch", "quota-management", "auto-fallback"]
    }

# ============ Anthropic 兼容接口 ============

@app.head("/anthropic")
async def anthropic_head():
    """Claude Code 启动探测"""
    return {}

@app.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic Messages API 兼容接口"""
    body = await request.json()
    api_key = verify_required(request)
    
    result = await anthropic_proxy_client.messages(
        api_key=api_key,
        body=body,
    )
    
    if isinstance(result, dict):
        return JSONResponse(content=result)
    return result


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)