# 用户手册：配置与运行 Tentacles

本手册面向人类使用者与运维者，介绍如何安装、配置信息源与大模型、运行定时流水线以及部署 Web 管理界面。

> 如果你在编写外部脚本或让 AI Agent 通过命令行消费采集到的资讯与报告，请参阅另一册 **[Agent CLI 数据消费手册](../agent-cli/README.md)**。

## 章节导航

1. **[基础配置与命名模型](configuration-and-models.md)**
   - `config/tentacles.yaml` 全局选项、`.env` 环境变量注入、配置校验
   - 命名模型（OpenAI 兼容 / Anthropic / OpenRouter）与五大阶段（`extract` / `filter` / `decision` / `persona` / `process`）路由、重试与计费规则
   - 有界面浏览器运行时（`browser` 块）：CDP 附着、Profile 核验、自动拉起与「绝不无头」边界
2. **[信息源配置与原位更新](sources.md)**
   - RSS / Atom 订阅
   - IMAP 邮箱只读增量抓取（Gmail 应用专用密码说明、文件夹游标与正文清洗）
   - Web 网页信息源（`list` CSS 选择器、`page` 内容版本差异、`llm` 智能抽取）
   - 有界面 Chrome 浏览器源（`type: browser`）：CDP + `chrome_profile` 附着、固化 Playwright 脚本与静态导入门禁
   - 可选原位更新（`update_existing: true`）与缓存语义
3. **[个人画像（Persona）、级联过滤与报告流水线](pipelines-and-personas.md)**
   - 版本化 Persona 种子、标签自动抽取、点赞/点踩反馈与提案演化
   - 级联过滤器：`keyword`（关键词/正则）、`decision`（JEV 相关性与标签判定）、`llm`（批量分类）
   - 报告处理器：`digest`（日报）、`deep`（事件聚类深度报道）、`summarize`（邮件简报与待办）、`none` 及自定义 Jinja 提示词
4. **[Web 管理界面、常驻服务与自动调度](server-and-web.md)**
   - 构建前端静态产物并启动 `tentacles serve`
   - Token 鉴权、CORS、防 DNS 重绑定与反向代理部署建议
   - 持久化调度语义（间隔与 Cron）、`launchd` 定时任务示例与完整 REST API 路由表

---

## 5 分钟快速开始

### 1. 安装后端并校验配置

要求 Python >= 3.10：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # 在 .env 中按需填入模型 API Key 或邮箱密码
tentacles config check            # 校验默认 config/tentacles.yaml
```

### 2. 手动抓取与查看条目

默认示例开启了中英文科技与新闻 RSS 源（纯 RSS 抓取无需填写任何模型 Key）：

```bash
tentacles sources fetch           # 并发抓取全部启用的信息源
tentacles items list --since 24h  # 查看最近 24 小时入库的条目
tentacles items show <ITEM_ID>    # 查看单条正文详情
```

### 3. 启动 Web 管理界面（可选）

要求 Node >= 22.12、pnpm 10.33：

```bash
# 构建前端产物（后端会自动托管 ../frontend/dist）
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..

# 启动服务（加 --no-scheduler 可只开网页与 API，不自动后台轮询）
tentacles serve
```

浏览器访问 `http://127.0.0.1:8000` 即可使用 **Feed**、**Sources**、**Pipelines**、**Reports**、**Personas**、**Runs** 和 **Settings** 页面。
