import yaml
from pathlib import Path
from typing import Dict, List, Union
from pydantic import BaseModel, Field
from functools import lru_cache


class ApiKeyConfig(BaseModel):
    key: str
    name: str
    description: str = ""


class ModelConfig(BaseModel):
    real_name: str
    daily_quota_tokens: int = 0
    monthly_quota_tokens: int = 0
    fallback_models: List[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key: str
    models: Dict[str, Union[str, ModelConfig]] = Field(default_factory=dict)


class ReasoningConfig(BaseModel):
    enabled: bool = True
    parameter: str = "reasoning_effort"
    mapping: Dict[str, str] = Field(default_factory=dict)


class GatewayConfig(BaseModel):
    api_keys: List[ApiKeyConfig]
    providers: List[ProviderConfig]
    reasoning: ReasoningConfig
    stats_file: str = "./token_stats.json"
    quota_file: str = "./quota_stats.json"
    quota_threshold: float = 0.95


class Config(BaseModel):
    gateway: GatewayConfig


@lru_cache()
def load_config(path: str = "config.yaml") -> Config:
    with open(Path(path), "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Config(**data)