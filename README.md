# tentacles

个人信息与新闻管理系统：从多种信息源抓取内容，按个人 persona 过滤，再用 LLM 加工成日报、深度报道或邮件简报。整体设计见 [#18](https://github.com/ljie-PI/tentacles/issues/18)，实施进度见它的子 issue。

> 目前已完成骨架：配置驱动的 RSS 抓取、去重入库、条目查询、运行记录和持久化调度。persona、过滤、LLM 处理、邮件和网页源、Web 端将陆续加入。

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
| `tentacles tick` | 并发执行到期的信息源任务 |
| `tentacles scheduler status` | 查看任务频率、到期时间、运行状态 |
| `tentacles runs list` | 查看运行记录 |

所有命令都支持 `--json`：stdout 只输出一个 JSON 值，日志写到 stderr，退出码按错误类型区分。给 agent 用的完整说明见 [docs/agent-cli.md](docs/agent-cli.md)。

## 自动抓取

`schedule` 支持正整数间隔（`30s`、`30m`、`2h`、`1d`、`1w`）、
`@hourly` / `@daily` / `@weekly`，或五段 cron（分、时、日、月、星期）。
cron 支持 `*`、范围、步长、列表（如 `0,30 9-17/2 * * 1-5`），星期 0 和 7 都是周日。
日和星期同时限定时取或，按 `app.timezone` 计算；夏令时不存在的时刻跳过，
回拨产生的两个真实时刻分别触发。非法频率在加载配置时拒绝。

间隔任务首次立即到期，cron 首次等待下一个触发点。状态保存在 SQLite 中，重启不会重置；
修改频率后以持久化的上次开始时间（尚未执行则用首次出现时间）重新计算。
成功和失败均推进到期时间。`sources fetch` 忽略频率立即执行（也推进调度时间），
但与 `tick` 共用原子任务锁；锁被占用时返回 `busy`，一小时后过期可抢占。

### launchd 示例（macOS）

保存为 `~/Library/LaunchAgents/local.tentacles.tick.plist`，把下面所有路径替换成你的绝对路径。
launchd 每 30 秒启动一次短进程，不需要另行运行常驻调度器；进程之间用 SQLite 锁防止重复抓取。
日志目录需要预先存在。密钥继续通过配置目录的 `.env` 加载，不要写进 plist。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.tentacles.tick</string>
  <key>ProgramArguments</key><array>
    <string>/absolute/path/tentacles/.venv/bin/tentacles</string>
    <string>--config</string><string>/absolute/path/tentacles/config/tentacles.yaml</string>
    <string>--json</string><string>tick</string>
  </array>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/absolute/path/tentacles/data/tick.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/tentacles/data/tick-error.log</string>
</dict></plist>
```

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
# 卸载：launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.tentacles.tick.plist
```

## 开发

```bash
pytest                 # 测试，不联网：HTTP 通过可注入的 transport 伪造
mypy src/tentacles     # 类型检查
```

测试都通过 Service 门面（`TentaclesService`）驱动。外部依赖只在边界处替换：HTTP 用 `tests/conftest.py` 里的 `FakeWeb`。
