import json
import httpx
from typing import AsyncGenerator, Optional, Dict, Any, Tuple
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from config_loader import load_config, ModelConfig
from token_tracker import TokenTracker, estimate_tokens_by_chars
from quota_manager import quota_manager


class ModelRouter:
    def __init__(self):
        self.config = load_config()
        self.tracker = TokenTracker()
        self._model_map: Dict[str, Tuple[Any, str, Any]] = {}
        for provider in self.config.gateway.providers:
            for alias, model_cfg in provider.models.items():
                if isinstance(model_cfg, ModelConfig):
                    real_name = model_cfg.real_name
                else:
                    real_name = str(model_cfg)
                self._model_map[alias] = (provider, real_name, model_cfg)

    def resolve(self, user_model: str) -> Tuple[Any, str, str, Any]:
        if user_model not in self._model_map:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{user_model}' not found. Available: {list(self._model_map.keys())}"
            )
        
        available = quota_manager.find_available_model(user_model)
        if not available:
            raise HTTPException(
                status_code=429,
                detail=f"Model '{user_model}' and all fallback models have exceeded their quotas"
            )
        
        final_alias, provider_name, real_model = available
        provider, _, model_cfg = self._model_map[final_alias]
        
        if final_alias != user_model:
            quota_manager.mark_switched(user_model, final_alias)
        
        return provider, real_model, final_alias, model_cfg

    def apply_reasoning(self, body: dict, reasoning_value: Optional[str]) -> dict:
        if not self.config.gateway.reasoning.enabled:
            return body
        if reasoning_value is None:
            return body

        param = self.config.gateway.reasoning.parameter
        mapping = self.config.gateway.reasoning.mapping
        actual_value = mapping.get(reasoning_value, reasoning_value)

        body = body.copy()
        body[param] = actual_value
        return body

    def _sanitize_messages(self, messages: list) -> list:
        """
        清洗 messages，修复 tool_call_id 缺失等问题
        """
        if not messages:
            return []
        
        sanitized = []
        tool_call_ids = set()
        
        # 第一遍：收集所有 tool_call_id
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id")
                    if tc_id:
                        tool_call_ids.add(tc_id)
        
        # 第二遍：清洗
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            
            msg = dict(msg)
            role = msg.get("role", "")
            
            # 1. tool 角色必须有 tool_call_id
            if role == "tool":
                tc_id = msg.get("tool_call_id")
                if not tc_id:
                    tc_id = msg.get("name", "")
                    if tc_id and tc_id in tool_call_ids:
                        msg["tool_call_id"] = tc_id
                    else:
                        msg["tool_call_id"] = "call_unknown"
                
                # 确保 content 是字符串
                content = msg.get("content")
                if content is None:
                    msg["content"] = ""
                elif not isinstance(content, str):
                    msg["content"] = json.dumps(content, ensure_ascii=False)
            
            # 2. assistant 的 tool_calls 补全字段
            elif role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if not tc.get("id"):
                        tc["id"] = f"call_{i}"
                    if not tc.get("type"):
                        tc["type"] = "function"
            
            # 3. function_call 旧格式兼容
            if "function_call" in msg:
                fc = msg["function_call"]
                if "tool_calls" not in msg:
                    msg["tool_calls"] = [{
                        "id": fc.get("name", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": fc.get("arguments", "{}")
                        }
                    }]
            
            # 4. content 不能为 None
            if msg.get("content") is None:
                msg["content"] = ""
            
            sanitized.append(msg)
        
        return sanitized


class ProxyClient:
    def __init__(self):
        self.router = ModelRouter()
        self.tracker = TokenTracker()
        self.timeout = httpx.Timeout(300.0, connect=30.0)

    async def chat_completions(
        self,
        api_key: str,
        body: dict,
        reasoning: Optional[str] = None,
    ) -> Any:
        user_model = body.get("model")
        
        provider, real_model, actual_alias, model_cfg = self.router.resolve(user_model)
        body = self.router.apply_reasoning(body, reasoning)
        body["model"] = real_model

        # 清洗 messages
        messages = body.get("messages", [])
        body["messages"] = self.router._sanitize_messages(messages)
        
        is_stream = body.get("stream", False)

        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }

        if is_stream:
            body.setdefault("stream_options", {})
            body["stream_options"]["include_usage"] = True

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    f"{provider.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=e.response.text,
                )

            if is_stream:
                return await self._handle_stream(
                    resp, api_key, user_model, actual_alias, provider.name, 
                    real_model, messages
                )
            else:
                return await self._handle_non_stream(
                    resp, api_key, user_model, actual_alias, provider.name,
                    real_model
                )

    async def _handle_non_stream(
        self,
        resp: httpx.Response,
        api_key: str,
        user_model: str,
        actual_alias: str,
        provider_name: str,
        real_model: str,
    ):
        data = resp.json()
        usage = data.get("usage", {})
        
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        tt = usage.get("total_tokens", pt + ct)

        self.tracker.record_usage(
            api_key=api_key,
            model=user_model,
            provider=provider_name,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
        )

        quota_manager.record_usage(actual_alias, pt, ct)

        data["model"] = user_model
        if actual_alias != user_model:
            data["gateway_info"] = {
                "original_model": user_model,
                "actual_model": actual_alias,
                "provider": provider_name,
                "reason": "quota_fallback",
            }
        
        return data

    async def _handle_stream(
        self,
        resp: httpx.Response,
        api_key: str,
        user_model: str,
        actual_alias: str,
        provider_name: str,
        real_model: str,
        original_messages: list,
    ):
        completion_text = []
        reasoning_text = []
        usage_data = {}
        first_chunk = True
        switched = actual_alias != user_model

        async def event_generator() -> AsyncGenerator[str, None]:
            nonlocal completion_text, reasoning_text, usage_data, first_chunk

            if switched:
                info_chunk = {
                    "id": "gateway-info",
                    "object": "chat.completion.chunk",
                    "created": int(__import__('time').time()),
                    "model": user_model,
                    "gateway_info": {
                        "original_model": user_model,
                        "actual_model": actual_alias,
                        "provider": provider_name,
                        "reason": "quota_fallback",
                    },
                    "choices": [],
                }
                yield f"data: {json.dumps(info_chunk, ensure_ascii=False)}\n\n"

            async for line in resp.aiter_lines():
                if not line:
                    continue
                
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    
                    if data_str == "[DONE]":
                        yield "data: [DONE]\n\n"
                        continue

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if first_chunk and chunk.get("model"):
                        chunk["model"] = user_model
                        first_chunk = False
                    elif chunk.get("model"):
                        chunk["model"] = user_model

                    if chunk.get("usage"):
                        usage_data = chunk["usage"]

                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        if delta.get("content"):
                            completion_text.append(delta["content"])
                        if delta.get("reasoning_content"):
                            reasoning_text.append(delta["reasoning_content"])

                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            # 流结束后统计
            ct = 0
            pt = 0

            if usage_data:
                pt = usage_data.get("prompt_tokens", 0)
                ct = usage_data.get("completion_tokens", 0)
            else:
                # 上游未返回 usage，用字符数估算
                full_completion = "".join(completion_text) + "".join(reasoning_text)
                ct = estimate_tokens_by_chars(full_completion)
                
                prompt_text = ""
                for msg in original_messages:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        prompt_text += content
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                prompt_text += item.get("text", "")
                pt = estimate_tokens_by_chars(prompt_text)

            tt = pt + ct

            self.tracker.record_usage(
                api_key=api_key,
                model=user_model,
                provider=provider_name,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
            )

            quota_manager.record_usage(actual_alias, pt, ct)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )


proxy_client = ProxyClient()