# 基础配置与命名模型

所有配置集中在一个 YAML 文件中，默认为 `config/tentacles.yaml`，也可通过全局参数 `--config/-c PATH` 或环境变量 `$TENTACLES_CONFIG` 指定。

## 1. 环境变量与路径解析

- **环境变量插值**：配置值中可写 `${VAR}` 或 `${VAR:-默认值}`。系统启动时会依次尝试读取项目根目录或配置文件所在目录的 `.env`，但**不会覆盖进程已存在的环境变量**。请始终将 API Key、邮箱密码、Server Token 存放在 `.env` 中。
- **相对路径基准**：`app.data_dir`、`app.reports_dir`、`personas.*.profile_file`、`processor.prompt_file`、`server.static_dir` 等所有相对路径，一律**相对于该 YAML 配置文件所在的目录**解析。

```yaml
app:
  timezone: Asia/Shanghai     # Web 展示与 5 段 cron 计算使用的 IANA 时区；SQLite 内一律存 UTC
  data_dir: ../data           # SQLite 数据库目录
  reports_dir: ../reports     # Markdown 报告落盘目录
```

## 2. 配置校验（`tentacles config check`）

修改 `tentacles.yaml` 后，建议先运行无副作用校验：

```bash
tentacles config check
```

- 一次性收集 YAML 结构、`${VAR}` 插值、Source/Pipeline ID 唯一性、模型阶段种类匹配、Jinja 提示词模板语法及 Persona 引用的全部错误，不会输出密钥或文件内容。
- 对已存在的数据库，会将其连同 WAL/journal 复制到私有临时目录做只读 Persona 名称核验，**绝不修改原库、不创建数据目录、不执行 schema 迁移或注册调度任务**。

---

## 3. 命名模型（`models`）与五阶段路由（`defaults.models`）

纯 RSS 抓取场景可以完全省略 `models` 与 `defaults`。一旦配置了 `models`，就必须在 `defaults.models` 中为以下 **5 个阶段**显式指定已存在且种类（`kind`）匹配的默认模型：

| 阶段 (`stage`) | 要求模型种类 (`kind`) | 用途与触发时机 |
| :--- | :--- | :--- |
| **`extract`** | `chat` | `type: web` + `mode: llm` 网页智能抽取（可在具体 source 上用 `model:` 覆盖） |
| **`filter`** | `chat` | Pipeline 中的 `- type: llm` 批量分类打分阶段（可在该 filter 上用 `model:` 覆盖） |
| **`decision`** | `decision` | Pipeline 中的 `- type: decision` 单条相关性与标签打分阶段（仅支持 OpenRouter Decisions API，如 JEV） |
| **`persona`** | `chat` | `persona set` 修改画像文本时自动抽取 5–20 个兴趣标签，以及 `persona evolve` 画像演化 |
| **`process`** | `chat` | Pipeline 处理器（`digest` / `deep` / `summarize`）生成最终报告（可在 processor 上用 `model:` 覆盖） |

### 配置示例

```yaml
models:
  fast:
    provider: openai
    model: deepseek-flash
    base_url: https://api.deepseek.com
    api_key: ${DEEPSEEK_API_KEY:-}
    max_tokens: 4096
    timeout: 60
    retries: 2
  smart:
    provider: anthropic
    model: claude-sonnet-5-5
    api_key: ${ANTHROPIC_API_KEY:-}
    max_tokens: 4096
  max:
    provider: anthropic
    model: claude-opus-5-5
    api_key: ${ANTHROPIC_API_KEY:-}
    max_tokens: 8192
  decision:
    provider: openrouter
    kind: decision
    model: typesafe/jev-1.13
    api_key: ${OPENROUTER_API_KEY:-}
    concurrency: 8
    extra_body:
      provider: {data_collection: deny}

defaults:
  models:
    extract: fast
    filter: fast
    decision: decision
    persona: max
    process: smart
```

### 模型字段参考

| 字段 | 含义与默认值 |
| :--- | :--- |
| `provider` | `openai`（默认，适用于所有 OpenAI 兼容接口如 DeepSeek、Moonshot、Ollama、vLLM）、`anthropic`、`openrouter` |
| `kind` | `chat`（默认）或 `decision`（仅限 `provider: openrouter`） |
| `model` | 服务商侧真实模型 ID（必填） |
| `base_url` | API 根路径（如本地 Ollama `http://localhost:11434/v1`）；缺省使用对应服务商官方地址；禁止包含凭据、query 或 fragment |
| `api_key` | 支持为空字符串；启动、`config check` 和 `models list` 时不联网且不报错，**只有实际发起模型调用时才检查非空**。本地无鉴权服务（Ollama/vLLM）请填任意非空占位值（如 `local`） |
| `temperature` | 可选 `0~2`，未配置则不在请求体中发送 |
| `max_tokens` | 正整数，默认 `4096`（Anthropic 始终发送；OpenAI 兼容接口若在 `extra_body` 指定了 `max_completion_tokens`，则自动省略旧 `max_tokens`） |
| `timeout` | 单次 HTTP 请求超时秒数，默认 `60` |
| `retries` | 首次请求失败后的最大重试次数，默认 `2` |
| `concurrency` | `decision` 模型每批并发固定 worker 上限，默认 `8` |
| `extra_body` | 原样合并进请求 JSON 的扩展参数；禁止覆盖 `model / messages / system / stream / state / questions` 等协议保留键 |

### 连通性测试、重试与安全边界

- **探测命令**：`tentacles models list` 查看密钥是否已配置（绝不回显明文）；`tentacles models test <NAME>`（或在 Web **Settings** 页点击测试）会发起一次真实轻量请求（**可能产生微量费用**），返回延迟、响应样例、Token 用量与服务商返回的 `cost`。
- **重试策略**：对 HTTP `408 / 409 / 425 / 429`、所有 `5xx`（含 `524 / 529`）、网络错误和超时自动重试。优先遵守响应头 `Retry-After`（支持秒数和 HTTP 日期，上限 60 秒），否则采用带抖动的指数退避（上限 30 秒）。不跟随 HTTP 重定向，防止 `Authorization` 头泄露到第三方域名。
- **密钥脱敏**：所有模型 `api_key` 注册进全局脱敏器，任何日志、异常诊断、报告正文或提取标题中若意外出现已配置的密钥文本，均会被强制替换为 `[REDACTED]`。
