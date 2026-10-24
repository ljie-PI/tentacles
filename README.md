# tentacles

个人信息与新闻管理系统：从多种信息源抓取内容，按个人 persona 过滤，再用 LLM 加工成日报、深度报道或邮件简报。整体设计见 [#18](https://github.com/ljie-PI/tentacles/issues/18)，实施进度见它的子 issue。

> 目前已完成骨架：配置驱动的 RSS 抓取、去重入库、条目查询和运行记录。persona、过滤、LLM 处理、调度、邮件和网页源、Web 端将陆续加入。

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # 可选：存放配置中 ${VAR} 引用的密钥
tentacles config check            # 校验 config/tentacles.yaml
tentacles sources fetch           # 抓取全部启用的信息源
tentacles items list --since 24h  # 查看最近 24 小时的条目
```

## 配置

所有配置都在一个 YAML 文件中，默认为 `config/tentacles.yaml`，也可以用 `--config` 或 `$TENTACLES_CONFIG` 指定：

- 值中可以写 `${VAR}` 或 `${VAR:-默认值}`，从环境变量读取。系统也会读取项目根目录或配置所在目录的 `.env`，但不会覆盖已有的环境变量。
- 相对路径都相对于配置文件所在的目录解析。
- 每个信息源需要声明 `id`、`type`、`schedule`；不同类型还有各自的选项，示例配置中有完整说明。

## 命令

| 命令 | 作用 |
| ---- | ---- |
| `tentacles config check` | 校验配置 |
| `tentacles sources list` | 列出信息源及其抓取状态 |
| `tentacles sources fetch [ID...]` | 立即抓取信息源，不写 ID 时抓取全部启用的源 |
| `tentacles items list` | 列出条目，可按来源、时间、关键词筛选，支持分页 |
| `tentacles items show ID` | 查看条目详情 |
| `tentacles runs list` | 查看运行记录 |

所有命令都支持 `--json`：stdout 只输出一个 JSON 值，日志写到 stderr，退出码按错误类型区分。给 agent 用的完整说明见 [docs/agent-cli.md](docs/agent-cli.md)。

## 开发

```bash
pytest                 # 测试，不联网：HTTP 通过可注入的 transport 伪造
mypy src/tentacles     # 类型检查
```

测试都通过 Service 门面（`TentaclesService`）驱动。外部依赖只在边界处替换：HTTP 用 `tests/conftest.py` 里的 `FakeWeb`。
