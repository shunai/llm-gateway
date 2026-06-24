import json
import httpx
from typing import AsyncGenerator, Optional, Dict, Any
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from config_loader import load_config
from proxy import ModelRouter
from token_tracker import TokenTracker
from quota_manager import quota_manager


class AnthropicProxyClient:
    def __init__(self):
        self.router = ModelRouter()
        self.tracker = TokenTracker()
        self.timeout = httpx.Timeout(300.0, connect=30.0)

    def _convert_to_openai_format(self, body: dict) -> dict:
        """将 Anthropic 请求转换为 OpenAI 格式"""
        messages = []
        
        # system 字段
        system = body.get("system")
        if system:
            if isinstance(system, str):
                messages.append({"role": "system", "content": system})
            elif isinstance(system, list):
                for item in system:
                    if isinstance(item, dict) and item.get("type") == "text":
                        messages.append({"role": "system", "content": item.get("text", "")})
                    else:
                        messages.append({"role": "system", "content": str(item)})
        
        # messages
        for msg in body.get("messages", []):
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                content = "\n".join(text_parts)
            
            messages.append({"role": role, "content": content})
        
        openai_body = {
            "model": body.get("model", ""),
            "messages": messages,
            "stream": body.get("stream", False),
            "max_tokens": body.get("max_tokens", 4096),
        }
        
        if "temperature" in body:
            openai_body["temperature"] = body["temperature"]
        if "top_p" in body:
            openai_body["top_p"] = body["top_p"]
        if "top_k" in body:
            openai_body["top_k"] = body["top_k"]
        
        return openai_body

    def _convert_to_anthropic_format(self, data: dict, model: str) -> dict:
        """将 OpenAI 响应转换为 Anthropic 格式"""
        usage = data.get("usage", {})
        
        return {
            "id": data.get("id", f"msg_{hash(json.dumps(data))}"),
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [
                {
                    "type": "text",
                    "text": data.get("choices", [{}])[0].get("message", {}).get("content", "")
                }
            ],
            "stop_reason": data.get("choices", [{}])[0].get("finish_reason", "end_turn"),
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            }
        }

    async def messages(
        self,
        api_key: str,
        body: dict,
    ) -> Any:
        user_model = body.get("model", "")
        
        # 解析模型
        provider, real_model, actual_alias, model_cfg = self.router.resolve(user_model)
        
        # 转换为 OpenAI 格式
        openai_body = self._convert_to_openai_format(body)
        openai_body["model"] = real_model
        
        is_stream = openai_body.get("stream", False)

        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }

        if is_stream:
            openai_body.setdefault("stream_options", {})
            openai_body["stream_options"]["include_usage"] = True

        # 构建正确的上游 URL
        upstream_url = f"{provider.base_url}/chat/completions"
        
        # 调试日志
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"DEBUG - Upstream URL: {upstream_url}")
        logger.info(f"DEBUG - Provider base_url: {provider.base_url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    upstream_url,
                    headers=headers,
                    json=openai_body,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=e.response.text,
                )

            if is_stream:
                return await self._handle_stream(
                    resp, api_key, user_model, actual_alias, provider.name, real_model
                )
            else:
                return await self._handle_non_stream(
                    resp, api_key, user_model, actual_alias, provider.name, real_model
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

        anthropic_resp = self._convert_to_anthropic_format(data, user_model)
        
        if actual_alias != user_model:
            anthropic_resp["gateway_info"] = {
                "original_model": user_model,
                "actual_model": actual_alias,
                "provider": provider_name,
                "reason": "quota_fallback",
            }
        
        return anthropic_resp

    async def _handle_stream(
        self,
        resp: httpx.Response,
        api_key: str,
        user_model: str,
        actual_alias: str,
        provider_name: str,
        real_model: str,
    ):
        completion_text = []
        usage_data = {}
        message_id = f"msg_{hash(str(resp))}"
        first_chunk = True

        async def event_generator() -> AsyncGenerator[str, None]:
            nonlocal completion_text, usage_data, first_chunk

            yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'model': user_model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"

            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"

            async for line in resp.aiter_lines():
                if not line:
                    continue
                
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    
                    if data_str == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("usage"):
                        usage_data = chunk["usage"]

                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        if delta.get("content"):
                            completion_text.append(delta["content"])
                            yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': delta['content']}})}\n\n"

            full_text = "".join(completion_text)
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

            ct = 0
            pt = 0
            if usage_data:
                pt = usage_data.get("prompt_tokens", 0)
                ct = usage_data.get("completion_tokens", 0)
            
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

            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': ct}})}\n\n"

            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )


anthropic_proxy_client = AnthropicProxyClient()