import json
import threading
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass

from config_loader import load_config


@dataclass
class ModelQuotaState:
    model_alias: str
    provider: str
    real_model: str
    daily_quota: int
    monthly_quota: int
    fallback_models: List[str]
    
    daily_used_tokens: int = 0
    daily_used_requests: int = 0
    monthly_used_tokens: int = 0
    monthly_used_requests: int = 0
    last_reset_day: str = ""
    last_reset_month: str = ""
    
    switched_to: Optional[str] = None
    switch_count_today: int = 0
    
    @property
    def daily_usage_rate(self) -> float:
        if self.daily_quota <= 0:
            return 0.0
        return self.daily_used_tokens / self.daily_quota
    
    @property
    def monthly_usage_rate(self) -> float:
        if self.monthly_quota <= 0:
            return 0.0
        return self.monthly_used_tokens / self.monthly_quota
    
    @property
    def is_near_limit(self) -> bool:
        threshold = load_config().gateway.quota_threshold
        return self.daily_usage_rate >= threshold or self.monthly_usage_rate >= threshold
    
    @property
    def is_exceeded(self) -> bool:
        if self.daily_quota > 0 and self.daily_used_tokens >= self.daily_quota:
            return True
        if self.monthly_quota > 0 and self.monthly_used_tokens >= self.monthly_quota:
            return True
        return False
    
    def record_usage(self, prompt_tokens: int, completion_tokens: int):
        total = prompt_tokens + completion_tokens
        self.daily_used_tokens += total
        self.daily_used_requests += 1
        self.monthly_used_tokens += total
        self.monthly_used_requests += 1


class QuotaManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.config = load_config()
        self.quota_file = Path(self.config.gateway.quota_file)
        self._states: Dict[str, ModelQuotaState] = {}
        self._file_lock = threading.Lock()
        self._build_states()
        self._load()
        self._check_and_reset()

    def _build_states(self):
        for provider in self.config.gateway.providers:
            for alias, model_cfg in provider.models.items():
                if isinstance(model_cfg, dict):
                    real_name = model_cfg.get("real_name", alias)
                    daily_quota = model_cfg.get("daily_quota_tokens", 0)
                    monthly_quota = model_cfg.get("monthly_quota_tokens", 0)
                    fallback = list(model_cfg.get("fallback_models", []))
                elif hasattr(model_cfg, "real_name"):
                    real_name = model_cfg.real_name
                    daily_quota = getattr(model_cfg, "daily_quota_tokens", 0)
                    monthly_quota = getattr(model_cfg, "monthly_quota_tokens", 0)
                    fallback = list(getattr(model_cfg, "fallback_models", []))
                else:
                    real_name = str(model_cfg)
                    daily_quota = 0
                    monthly_quota = 0
                    fallback = []
                
                self._states[alias] = ModelQuotaState(
                    model_alias=alias,
                    provider=provider.name,
                    real_model=real_name,
                    daily_quota=daily_quota,
                    monthly_quota=monthly_quota,
                    fallback_models=fallback,
                )

    def _load(self):
        if not self.quota_file.exists():
            return
            
        try:
            with open(self.quota_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for alias, state_data in data.items():
                if alias in self._states:
                    existing = self._states[alias]
                    existing.daily_used_tokens = state_data.get("daily_used_tokens", 0)
                    existing.daily_used_requests = state_data.get("daily_used_requests", 0)
                    existing.monthly_used_tokens = state_data.get("monthly_used_tokens", 0)
                    existing.monthly_used_requests = state_data.get("monthly_used_requests", 0)
                    existing.last_reset_day = state_data.get("last_reset_day", "")
                    existing.last_reset_month = state_data.get("last_reset_month", "")
                    existing.switch_count_today = state_data.get("switch_count_today", 0)
                    existing.switched_to = state_data.get("switched_to", None)
        except Exception:
            pass

    def _save(self):
        """手动构建纯 dict，避免任何不可序列化对象"""
        with self._file_lock:
            data = {}
            for alias, state in self._states.items():
                data[alias] = {
                    "model_alias": state.model_alias,
                    "provider": state.provider,
                    "real_model": state.real_model,
                    "daily_quota": state.daily_quota,
                    "monthly_quota": state.monthly_quota,
                    "fallback_models": state.fallback_models,
                    "daily_used_tokens": state.daily_used_tokens,
                    "daily_used_requests": state.daily_used_requests,
                    "monthly_used_tokens": state.monthly_used_tokens,
                    "monthly_used_requests": state.monthly_used_requests,
                    "last_reset_day": state.last_reset_day,
                    "last_reset_month": state.last_reset_month,
                    "switched_to": state.switched_to,
                    "switch_count_today": state.switch_count_today,
                }
            with open(self.quota_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def _check_and_reset(self):
        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")
        
        need_save = False
        for state in self._states.values():
            if state.last_reset_day != today:
                state.daily_used_tokens = 0
                state.daily_used_requests = 0
                state.switch_count_today = 0
                state.switched_to = None
                state.last_reset_day = today
                need_save = True
            
            if state.last_reset_month != this_month:
                state.monthly_used_tokens = 0
                state.monthly_used_requests = 0
                state.last_reset_month = this_month
                need_save = True
        
        if need_save:
            self._save()

    def get_state(self, model_alias: str) -> Optional[ModelQuotaState]:
        self._check_and_reset()
        return self._states.get(model_alias)

    def record_usage(self, model_alias: str, prompt_tokens: int, completion_tokens: int):
        self._check_and_reset()
        if model_alias in self._states:
            self._states[model_alias].record_usage(prompt_tokens, completion_tokens)
            self._save()

    def find_available_model(self, model_alias: str) -> Optional[Tuple[str, str, str]]:
        self._check_and_reset()
        
        visited = set()
        queue = [model_alias]
        
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            
            state = self._states.get(current)
            if not state:
                continue
            
            if not state.is_exceeded:
                return (current, state.provider, state.real_model)
            
            for fallback in state.fallback_models:
                if fallback not in visited:
                    queue.append(fallback)
        
        return None

    def mark_switched(self, from_model: str, to_model: str):
        self._check_and_reset()
        if from_model in self._states:
            self._states[from_model].switched_to = to_model
            self._states[from_model].switch_count_today += 1
            self._save()

    def get_all_states(self) -> Dict[str, ModelQuotaState]:
        self._check_and_reset()
        return self._states.copy()

    def get_quota_report(self) -> dict:
        self._check_and_reset()
        report = {
            "generated_at": datetime.now().isoformat(),
            "threshold": self.config.gateway.quota_threshold,
            "models": {}
        }
        for alias, state in self._states.items():
            report["models"][alias] = {
                "provider": state.provider,
                "real_model": state.real_model,
                "daily": {
                    "quota": state.daily_quota,
                    "used_tokens": state.daily_used_tokens,
                    "used_requests": state.daily_used_requests,
                    "usage_rate": round(state.daily_usage_rate * 100, 2),
                    "near_limit": state.daily_usage_rate >= self.config.gateway.quota_threshold,
                    "exceeded": state.daily_used_tokens >= state.daily_quota if state.daily_quota > 0 else False,
                },
                "monthly": {
                    "quota": state.monthly_quota,
                    "used_tokens": state.monthly_used_tokens,
                    "used_requests": state.monthly_used_requests,
                    "usage_rate": round(state.monthly_usage_rate * 100, 2),
                    "near_limit": state.monthly_usage_rate >= self.config.gateway.quota_threshold,
                    "exceeded": state.monthly_used_tokens >= state.monthly_quota if state.monthly_quota > 0 else False,
                },
                "fallback_models": state.fallback_models,
                "switched_to": state.switched_to,
                "switch_count_today": state.switch_count_today,
            }
        return report


quota_manager = QuotaManager()