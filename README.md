# tentacles

抓取多来源新闻 feed（RSS/Atom），聚合去重后按事件聚类，并用 LLM 生成 **中文新闻日报**；对文章数量多、多来源同时报道的**重大事件**，另行生成深度报道文章。

> 前身为一个 Java 分布式爬虫框架，已 re-purpose 为 feed 聚合 + 新闻生成 项目。

## 工作流程

```
feeds.yaml (RSS/Atom 源列表)
        │  并发抓取 (httpx + feedparser)
        ▼
   Article（URL 规范化 / 指纹去重 / 标题相似去重）
        ▼
   SQLite (data/tentacles.db)
        ▼
   事件聚类（标题相似度贪心聚类）
        ▼
   LLM 生成
    ├── 日报 (reports/YYYY-MM-DD-daily.md)
    └── 深度报道 (reports/YYYY-MM-DD-deep-<事件>.md)，事件文章数 >= 3 时触发
```

## 快速开始

```bash
# 安装
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" 2>/dev/null || pip install -e .
pip install pytest

# 配置 LLM（OpenAI 兼容接口即可：OpenAI / DeepSeek / Moonshot / 本地 vLLM…）
cp .env.example .env   # 填入 API key / base_url / model

# 抓取 feed → 入库
tentacles fetch

# 生成日报 + 重大事件深度报道
tentacles daily
```

## 命令

| 命令 | 作用 |
| ---- | ---- |
| `tentacles fetch` | 抓取全部 feed，去重后写入 SQLite |
| `tentacles daily --hours 24` | 对最近 N 小时文章聚类并生成日报；重大事件同时生成深度报道 |
| `tentacles list` | 查看已配置的 feed 源 |

## 配置

- **feed 源**：编辑 `feeds.yaml`
- **LLM / 存储路径 / 抓取超时**：环境变量或 `.env`（见 `.env.example`，前缀 `TENTACLES_`）
- **深度报道触发阈值**：`tentacles/cluster.py` 中的 `MAJOR_EVENT_SIZE`
- **去重 / 聚类相似度阈值**：`tentacles/dedup.py`、`tentacles/cluster.py`

## 目录结构

```
src/tentacles/
├── cli.py          # typer 命令行入口
├── config.py       # 设置 + feeds.yaml 加载
├── models.py       # Article 模型、URL 规范化、内容指纹
├── sources/        # feed 抓取 (httpx + feedparser)
├── dedup.py        # 标题相似度去重
├── cluster.py      # 事件聚类，重大事件判定
├── store.py        # SQLite 存储
└── llm/            # OpenAI 兼容客户端 + 日报/深度报道 prompt
```

## 测试

```bash
pytest tests/
```
