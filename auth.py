from typing import Optional
from fastapi import HTTPException, Header, Query
from config_loader import load_config


class AuthManager:
    def __init__(self):
        self.config = load_config()
        self._keys = {item.key: item for item in self.config.gateway.api_keys}

    def verify(self, authorization: str = Header(None)) -> str:
        """强制认证：用于 chat/completions 等敏感接口"""
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")

        parts = authorization.split()
        api_key = parts[-1] if parts else authorization

        if api_key not in self._keys:
            raise HTTPException(status_code=401, detail="Invalid API Key")

        return api_key

    def verify_optional(
        self,
        authorization: str = Header(None),
        api_key: str = Query(None),
    ) -> Optional[str]:
        """
        可选认证：支持 Header Bearer Token 或 Query 参数 ?api_key=xxx
        用于 /v1/models 等发现接口，允许匿名访问
        """
        key = None
        if authorization:
            parts = authorization.split()
            key = parts[-1] if parts else authorization
        elif api_key:
            key = api_key

        if key is None:
            return None

        if key not in self._keys:
            raise HTTPException(status_code=401, detail="Invalid API Key")

        return key

    def get_key_info(self, api_key: str):
        return self._keys.get(api_key)


auth_manager = AuthManager()