<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" />
</p>

<h1 align="center">🚀 LLM Gateway</h1>

<p align="center">
  <b>零数据库 · 纯配置驱动 · 企业级大模型统一网关</b>
</p>

<p align="center">
  一行配置接入多模型，智能配额管理，自动故障转移，让 AI 基础设施稳如磐石。
</p>

---

## ✨ 核心亮点

| 特性 | 说明 |
|------|------|
| 🔑 **统一 API Key** | 一个 BaseURL + 一个 Key，无缝切换所有模型 |
| 👥 **团队级隔离**        | 每个用户/团队独立 API Key，使用数据完全隔离，互不干扰                |
| 🤖 **完美支持多 Agent** | Hermes、Claude Code、Cline、Cursor、Continue 等开箱即用 |
| 🔄 **智能模型映射** | `mimo-v2.5-pro` → `kimi-k2-6`，对外暴露友好名称，底层自由切换 |
| 📊 **精准 Token 统计** | 完全依赖上游 API 返回的真实 usage，不瞎估算 |
| 🛡️ **配额熔断 + 自动降级** | 日/月配额接近 95% 时，自动按优先级切换到备用模型，业务零中断 |
| ⚡ **流式输出** | SSE 原生支持，首 token 延迟不增加 |
| 🧠 **思考开关** | `X-Reasoning: on/auto/off` 一键控制推理深度 |
| 📁 **零数据库** | 纯 YAML + JSON 文件驱动，部署只需 `python main.py` |
| 🔌 **多协议兼容** | 同时兼容 OpenAI、Ollama、OpenWebUI 等协议 |

---

## 🚀 安装步骤

### 环境要求

- Python 3.10+
- pip

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd llm-gateway
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv

# macOS/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 API Key

复制并编辑配置文件：

```bash
cp config.yaml.example config.yaml
# 或直接用编辑器打开
vim config.yaml
```

填入你的上游模型 API Key：

```yaml
gateway:
  api_keys:
    - key: "sk-gateway-user1-abc123"
      name: "张三"
      description: "张三专属"
    - key: "sk-gateway-user2-abc123"
      name: "李四"
      description: "李四专属"

  providers:
    - name: "xiaomi"
      base_url: "https://token-plan-cn.xiaomimimo.com/v1"
      api_key: "tp-your-mimo-api-key-here"
      models:
        "mimo-v2.5-pro":
          real_name: "mimo-v2.5-pro"
          daily_quota_tokens: 1000000
          monthly_quota_tokens: 30000000
          fallback_models: ["mimo-v2.5"]
        "mimo-v2.5":
          real_name: "mimo-v2.5"
          daily_quota_tokens: 2000000
          fallback_models: []
```

### 5. 启动服务

```bash
python main.py
```

服务默认运行在 `http://localhost:8000`

---

## 🤖 Agent 配置手册

### Hermes

编辑 `~/.hermes/config.yaml`：

```yaml
providers:
  - name: "mimo-gateway"
    type: "custom"
    api_key: "sk-gateway-user1-abc123"
    base_url: "http://localhost:8000/v1"
    model: "mimo-v2.5-pro"
```

### Claude Code

Claude Code 通过环境变量或启动参数配置自定义 Provider：

```bash
# 方式一：环境变量
export ANTHROPIC_BASE_URL="http://localhost:8000/v1"
export ANTHROPIC_AUTH_TOKEN="sk-gateway-user1-abc123"

claude

# 方式二：启动参数
claude --provider "openai" \
       --base-url "http://localhost:8000/v1" \
       --api-key "sk-gateway-user1-abc123"
```

或在 `~/.claude/settings.json` 中配置：

```json
{
  "provider": "openai",
  "openAiBaseUrl": "http://localhost:8000/v1",
  "openAiKey": "sk-gateway-user1-abc123",
  "model": "mimo-v2.5-pro"
}
```

### Cline (VS Code 插件)

在 Cline 设置中选择 **OpenAI Compatible**：

| 字段 | 值 |
|------|-----|
| Base URL | `http://localhost:8000/v1` |
| API Key | `sk-gateway-user1-abc123` |
| Model ID | `mimo-v2.5-pro` |

### Cursor

设置 → Models → OpenAI API：

- **OpenAI API Key**: `sk-gateway-user1-abc123`
- **OpenAI Base URL**: `http://localhost:8000/v1`
- **Model**: 自定义输入 `mimo-v2.5-pro`

### Continue (VS Code / JetBrains)

`~/.continue/config.json`：

```json
{
  "models": [
    {
      "title": "Mimo Gateway",
      "provider": "openai",
      "model": "mimo-v2.5-pro",
      "apiKey": "sk-gateway-user1-abc123",
      "apiBase": "http://localhost:8000/v1"
    }
  ]
}
```

### OpenWebUI

管理面板 → 设置 → 外部连接：

- **API Base URL**: `http://localhost:8000/v1`
- **API Key**: `sk-gateway-user1-abc123`

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="mimo-v2.5-pro",
    api_key="sk-gateway-user1-abc123",
    base_url="http://localhost:8000/v1",
)
```

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-gateway-user1-abc123",
    base_url="http://localhost:8000/v1",
)

response = client.chat.completions.create(
    model="mimo-v2.5-pro",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
```

---

## 📡 API 使用手册

### 对话接口

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-user1-abc123" \
  -H "Content-Type: application/json" \
  -H "X-Reasoning: on" \
  -d '{
    "model": "mimo-v2.5-pro",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 列出模型

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-user1-abc123"
```

### 健康检查

```bash
curl http://localhost:8000/health
```

---

## 📊 Token 统计接口

### 查询指定 API Key 的用量

```bash
curl "http://localhost:8000/v1/usage?key=sk-gateway-user1-abc123"
```

**返回示例：**

```json
{
  "status": "success",
  "data": {
    "api_key": "sk-gateway-user1-abc123",
    "name": "研发团队",
    "total_prompt_tokens": 15234,
    "total_completion_tokens": 48921,
    "total_tokens": 64155,
    "requests": 128,
    "daily": {
      "2026-06-23": {
        "prompt_tokens": 1024,
        "completion_tokens": 4096,
        "total_tokens": 5120,
        "requests": 10
      },
      "2026-06-22": {
        "prompt_tokens": 14210,
        "completion_tokens": 44825,
        "total_tokens": 59035,
        "requests": 118
      }
    },
    "models": {
      "xiaomi/mimo-v2.5-pro": {
        "prompt_tokens": 15234,
        "completion_tokens": 48921,
        "total_tokens": 64155,
        "requests": 128
      }
    }
  }
}
```

### 查询全局配额报告

```bash
curl http://localhost:8000/v1/quota \
  -H "Authorization: Bearer sk-gateway-user1-abc123"
```

**返回示例：**

```json
{
  "status": "success",
  "data": {
    "generated_at": "2026-06-23T15:30:00",
    "threshold": 0.95,
    "models": {
      "mimo-v2.5-pro": {
        "provider": "xiaomi",
        "real_model": "mimo-v2.5-pro",
        "daily": {
          "quota": 1000000,
          "used_tokens": 950000,
          "used_requests": 500,
          "usage_rate": 95.0,
          "near_limit": true,
          "exceeded": false
        },
        "monthly": {
          "quota": 30000000,
          "used_tokens": 15200000,
          "used_requests": 8000,
          "usage_rate": 50.67,
          "near_limit": false,
          "exceeded": false
        },
        "fallback_models": ["mimo-v2.5"],
        "switched_to": "mimo-v2.5",
        "switch_count_today": 12
      }
    }
  }
}
```

### 查询全局统计

```bash
curl http://localhost:8000/v1/stats \
  -H "Authorization: Bearer sk-gateway-user1-abc123"
```

---

## 🧠 智能降级示例

当 `mimo-v2.5-pro` 的日配额使用达到 **95%**：

```
请求: mimo-v2.5-pro
   │
   ▼ 配额检查
┌─────────────────┐
│ 已用 950K/1M   │  ◄── 触发阈值 95%
│ 自动降级中...   │
└─────────────────┘
   │
   ▼
响应: mimo-v2.5 (fallback)
   │
   ▼ 响应头附带
gateway_info: {
  "original_model": "mimo-v2.5-pro",
  "actual_model": "mimo-v2.5",
  "reason": "quota_fallback"
}
```

**业务无感知，成本可控，服务不中断。**

---

## 🛠️ 配置详解

```yaml
gateway:
  api_keys:
    - key: "sk-gateway-user1-abc123"    # 网关统一 API Key
      name: "研发团队"                   # 标识名称

  providers:
    - name: "xiaomi"                    # Provider 标识
      base_url: "https://..."           # 上游 API 地址
      api_key: "tp-xxx"                 # 上游 API Key
      models:
        "mimo-v2.5-pro":                # 对外暴露的模型名
          real_name: "mimo-v2.5-pro"   # 上游真实模型名
          daily_quota_tokens: 1000000   # 日限额 (0 = 不限)
          monthly_quota_tokens: 30000000 # 月限额
          fallback_models:              # 降级优先级队列
            - "mimo-v2.5"

  reasoning:
    enabled: true
    parameter: "reasoning_effort"
    mapping:
      "on": "high"
      "auto": "medium"
      "off": "low"

  stats_file: "./token_stats.json"      # Token 统计持久化文件
  quota_file: "./quota_stats.json"      # 配额状态持久化文件
  quota_threshold: 0.95                  # 自动降级阈值
```

---

## 📄 License

MIT License — 自由使用，欢迎贡献。
