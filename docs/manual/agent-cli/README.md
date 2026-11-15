# Agent CLI 手册：消费采集信息与操控画像

本手册专为调用 `tentacles` 命令行的 AI Agent、MCP 工具或自动化脚本编写，说明如何以确定性的 JSON 契约读取已采集的条目、匹配分数、生成报告，以及通过命令行反馈与更新个人画像（Persona）。

> 如需了解如何编写 `config/tentacles.yaml`、配置信息源/模型或部署 Web 服务，请参阅 **[用户配置与运行手册](../user/README.md)**。

## 章节导航

1. **[读取与消费采集结果（Items & Reports）](consuming-items-and-reports.md)**
   - 如何按时间、来源、关键词、Pipeline 通过/淘汰状态检索条目
   - 每条内容包含哪些字段（链接、摘要、正文、兴趣标签、评估分数与多阶段评分轨迹）
   - 如何读取已生成的日报、深度报道与邮件待办简报
2. **[通过 Agent CLI 反馈与更新 Persona](persona-and-feedback.md)**
   - 对采集条目点赞/点踩（`feedback` / `items feedback`）
   - 直接修改画像与关键词（`persona set`）、带乐观锁并发保护（`--expected-version`）
   - 基于反馈与指令预览演化提案并管道应用（`persona evolve --dry-run | persona apply`）及版本回滚
3. **[触发采集、流水线与运维状态速查](operations-reference.md)**
   - 手动抓取信息源（`sources fetch`）、触发过滤/成稿（`pipelines filter/run`）、执行周期 `tick`
   - 检查配置（`config check`）、调度进度（`scheduler status`）与运行计费账本（`runs list`）
   - 回滚安全的故障账本（`errors list --unresolved`、`errors show`、`runs show`）、`error_code` 速查表与 `failed_targets` 排障工作流

---

## 全局调用契约（Agent 必读）

### 1. 始终启用 JSON 模式

Agent 调用任何子命令时，**必须**将全局参数写在子命令之前，或设置环境变量 `TENTACLES_JSON=1`：

```bash
tentacles --json items list --since 24h --limit 50
# 或指定自定义配置文件：
tentacles --config ./config/tentacles.yaml --json items show <ITEM_ID>
```

| 全局选项 | 说明 |
| :--- | :--- |
| `--config, -c PATH` | 配置文件路径；省略时依次读取 `$TENTACLES_CONFIG`、`./config/tentacles.yaml` |
| `--json` | 强制 `stdout` 仅输出**唯一一个合法 JSON 值**（也可设 `TENTACLES_JSON=1`） |
| `-v, --verbose` | 将 `info` 级别运行日志输出到 `stderr`（默认仅输出 `warning` 及以上） |

### 2. 标准流与错误退出码映射

- **`stdout`**：严格只输出**一个 JSON 值**。成功时是业务数据对象/数组；失败时（包括未知命令、缺少参数、类型越界等 CLI 参数错误）一律输出结构化错误对象，绝不混杂人类 Usage 帮助文本：
  ```json
  {"error": {"code": "not_found", "message": "unknown item 'abc'"}}
  ```
- **`stderr`**：所有运行日志均写向 `stderr`，解析 JSON 时请只读取 `stdout`。
- **时间格式**：所有返回的日期时间字符串一律为 **UTC ISO-8601**（如 `"2026-10-25T03:15:00Z"`）。

| 退出码 (`$?`) | `error.code` | 含义与 Agent 处理建议 |
| :--- | :--- | :--- |
| **`0`** | — | 命令执行完成。**注意**：批量/探测命令（`sources fetch`、`tick`、`pipelines run`、`models test`）即使单项目标失败，退出码仍为 `0`，**必须检查返回 JSON 内的 `status` 或 `ok` 字段** |
| **`2`** | `invalid` | 参数错误或配置校验失败（如非法时间格式、`limit` 超出 `1..500`）—— 修正参数，勿盲目重试 |
| **`3`** | `not_found` | 指定的 Source、Item、Persona、Pipeline 或 Report ID 不存在 |
| **`4`** | `conflict` / `busy` | Persona 乐观锁版本冲突（需刷新最新 `version` 重试），或目标任务正在被其他进程执行 |
| **`5`** | `upstream` | 外部上游失败（大模型 API、IMAP 邮箱或目标站点报错） |
| **`1`** | `error` | 未预期的内部异常（已脱敏） |
| **`130`** | `aborted` | 进程收到中断信号（SIGINT / Ctrl-C） |

---

## 最常用三步消费工作流

当用户问 Agent *“今天抓到了什么值得看的内容？”* 时：

```bash
# 第一步：拉取最近 24 小时已通过指定 Pipeline 兴趣筛选的条目（含分数、标签与理由）
tentacles --json items list --pipeline daily-tech --passed --since 24h --limit 50

# 第二步：对高分或感兴趣的条目读取正文与多阶段评估详情（或顺着条目的 url 访问源网页）
tentacles --json items show 6912c034fd032f1e

# 第三步（可选）：直接读取最新一期已汇总好的 Markdown 日报
tentacles --json reports list --pipeline daily-tech --limit 1
tentacles --json reports show <REPORT_ID>
```
