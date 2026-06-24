import json
import threading
import re
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

from config_loader import load_config


class TokenTracker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.config = load_config()
        self.stats_file = Path(self.config.gateway.stats_file)
        self._stats: Dict[str, dict] = {}
        self._file_lock = threading.Lock()
        self._load()

    def _load(self):
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    self._stats = json.load(f)
            except Exception:
                self._stats = {}

    def _save(self):
        with self._file_lock:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self._stats, f, ensure_ascii=False, indent=2)

    def record_usage(
        self,
        api_key: str,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
    ):
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens

        today = datetime.now().strftime("%Y-%m-%d")
        
        if api_key not in self._stats:
            self._stats[api_key] = {
                "name": "",
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "requests": 0,
                "daily": {},
                "models": {},
            }

        stats = self._stats[api_key]
        stats["total_prompt_tokens"] += prompt_tokens
        stats["total_completion_tokens"] += completion_tokens
        stats["total_tokens"] += total_tokens
        stats["requests"] += 1

        if today not in stats["daily"]:
            stats["daily"][today] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "requests": 0,
            }
        d = stats["daily"][today]
        d["prompt_tokens"] += prompt_tokens
        d["completion_tokens"] += completion_tokens
        d["total_tokens"] += total_tokens
        d["requests"] += 1

        model_key = f"{provider}/{model}"
        if model_key not in stats["models"]:
            stats["models"][model_key] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "requests": 0,
            }
        m = stats["models"][model_key]
        m["prompt_tokens"] += prompt_tokens
        m["completion_tokens"] += completion_tokens
        m["total_tokens"] += total_tokens
        m["requests"] += 1

        self._save()

    def get_stats(self, api_key: Optional[str] = None) -> dict:
        if api_key:
            return self._stats.get(api_key, {})
        return self._stats.copy()

    def set_key_name(self, api_key: str, name: str):
        if api_key in self._stats:
            self._stats[api_key]["name"] = name
            self._save()


def estimate_tokens_by_chars(text: str) -> int:
    """
    粗略估算（仅作为上游不返回 usage 时的 fallback）：
    - 中文字符：约 1.5 字符 = 1 token
    - 英文/其他：约 4 字符 = 1 token
    """
    if not text:
        return 0
    
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - chinese_chars
    
    return max(1, int(chinese_chars / 1.5 + other_chars / 4))