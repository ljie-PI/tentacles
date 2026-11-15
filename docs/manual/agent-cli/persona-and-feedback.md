# 通过 Agent CLI 反馈与更新 Persona

Persona（个人兴趣画像）存储在 SQLite 中并采用**不可变追加版本**机制。外部 Agent **完全可以通过 CLI 查看、直接编辑、打标反馈、驱动 LLM 演化以及回滚 Persona**，无需触碰数据库或重启服务。

---

## 1. 查看画像、历史版本与导出快照

```bash
# 列出当前所有 Persona 的最新版本
tentacles --json persona list

# 查看指定名称的最新版本（或加 --version N 查看历史某版）
tentacles --json persona show default
tentacles --json persona show default --version 1

# 查看某个 Persona 的完整版本演进历史（按版本号倒序）
tentacles --json persona history default

# 导出完整 YAML 版本快照（--json 模式下 stdout 是一个包含 YAML 文本的 JSON 字符串）
tentacles --json persona export default
```

`PersonaView` 返回示例：

```json
{
  "name": "default",
  "version": 3,
  "profile": "优先关注人工智能与开源软件的实质进展……",
  "tags": [
    {"tag": "AI与开源模型", "description": "大模型架构、本地推理、开源权重与评测代码"},
    {"tag": "机器人与具身智能", "description": "人形机器人、运动控制与硬件突破"}
  ],
  "include": ["人工智能", "AI", "开源"],
  "exclude": ["广告", "促销"],
  "source": "llm",
  "note": "增加对本地推理量化评测的权重，过滤无实测数据的融通稿",
  "created_at": "2026-10-25T08:00:00Z",
  "tag_status": "ok",
  "tag_error": null
}
```

- `source` 取值：`seed`（首次配置导入）、`manual`（手动 `set`）、`llm`（`evolve / apply` 演化）、`rollback`（历史回滚）。
- `tag_status` 取值：`ok`、`error`（标签抽取调用失败但画像文本已安全保存并保留了旧标签）、`skipped`（指定了 `--skip-tags` 或种子导入）、`unchanged`（仅改关键词/备注未动正文）。

---

## 2. 直接编辑画像与关键词：`persona set`

当用户明确告诉 Agent *“把‘具身智能’加进我的包含关键词”* 或 *“帮我重写一下兴趣画像”* 时，使用 `persona set`（若名称尚不存在则创建第 1 版）：

```bash
# 1. 仅增删关键词（零模型调用，支持并发自动合并）
tentacles --json persona set default \
  --include "具身智能" \
  --exclude "带货" \
  --remove-exclude "促销" \
  --note "按用户要求微调关键词"

# 2. 更新完整 profile 文本（默认会自动调用 persona 阶段模型重抽 5~20 个有序标签）
tentacles --json persona set default \
  --text "优先关注本地大模型推理、Rust 系统编程与开源工具链……" \
  --expected-version 3 \
  --note "聚焦本地推理与系统编程"

# 3. 从文件或标准输入传入长文本，并跳过标签模型调用（零费用）
cat ./new-profile.md | tentacles --json persona set default --stdin --skip-tags
```

| 选项 | 说明 |
| :--- | :--- |
| `--text TEXT` / `--file PATH` / `--stdin` | 三选一替换 `profile` 正文（不传则保留原 `profile`） |
| `--include KEY` / `--exclude KEY` | 新增包含或排除关键词（可重复传入；大小写不敏感去重） |
| `--remove-include KEY` / `--remove-exclude KEY` | 删除已有关键词（同次命令中先执行删除再执行新增） |
| `--skip-tags` | 即使修改了 `profile` 也不调用大模型重抽标签，直接沿用旧标签 |
| `--expected-version N` | **推荐 Agent 传入**：乐观锁并发围栏（CAS）。新建传 `0`；编辑传刚读到的 `version`。若期间被 Web 端或其他进程抢先修改，立即返回退出码 `4`（`conflict`），防止静默覆盖 |
| `--note TEXT` | 记录在版本历史中的变更说明 |

---

## 3. 对条目点赞/点踩并驱动画像演化（Feedback → Evolve → Apply）

如果用户并非想亲手改写长篇画像，而是在阅读资讯时评价某些条目好或不好，或者下达一句模糊调整指令（如 *“最近标题党太多，多关注有代码和实验数据的帖子”*），Agent 应走**反馈与提案演化工作流**：

### 第一步：记录或查询单条反馈

```bash
# 点踩并附带具体原因（演化模型会将条目标题、摘要和你的 note 作为有界证据参考）
tentacles --json feedback 6912c034fd032f1e --dislike --persona default --note "纯融资公关稿，无技术细节"

# 点赞
tentacles --json feedback 8f31a902bc114e09 --like --persona default --note "有完整量化实测对比"

# 查询某条目在指定 Persona 下的最新投票状态（无记录返回 null）
tentacles --json items feedback 6912c034fd032f1e --persona default
```

*(注：若系统中只有一个 Persona，或操作名为 `default` 的 Persona，`--persona` 可省略。)*

### 第二步：生成演化提案预览（`--dry-run`）并确认后应用（`apply`）

推荐先用 `--dry-run` 生成 JSON 提案供检查（会调用 `defaults.models.persona` 模型，但**在只读内存快照上运行，绝不修改数据库**），确认 `summary`、新 `profile`、`tags` 和关键词合理后，再调用 `persona apply` 原子落库（`apply` **不再调用模型**）：

```bash
# 1. 生成演化提案并保存为文件（默认自动结合最近窗口内的新反馈；加 --no-feedback 可仅凭指令演化）
tentacles --json persona evolve default \
  --instruction "提高对本地量化实测的优先级，严格剔除无基准数据的公关稿" \
  --expected-version 3 \
  --dry-run > proposal.json

# 2. 检查 proposal.json 中的 summary / profile / tags / include / exclude 后，原子应用：
tentacles --json persona apply --proposal proposal.json

# （若无需人工二次审阅，也可直接管道一步串接，或省略 --dry-run 直接应用）
tentacles --json persona evolve default --instruction "多关注机器人" --dry-run \
  | tentacles --json persona apply --proposal -
```

- 如果在生成提案到执行 `apply` 之间，Persona 的基准版本 `base_version` 已被其他操作改动，`apply` 会安全拒绝并返回退出码 `4`（`conflict`）。

### 第三步（可选）：对近期条目按新画像重新打分

Persona 升级到新版本后，如果希望立即用新画像重算最近 7 天窗口内的条目分数：

```bash
tentacles --json pipelines filter daily-tech --reevaluate --since 7d
```

*(注：`--reevaluate` 只重算当前 Persona 版本在窗口内的评估得分，**绝不会清空历史消费账本**，因此过去已经发过日报的旧条目不会被重复写进新日报。)*

---

## 4. 安全回滚历史版本：`persona rollback`

发现某次修改或演化效果不佳时，可随时回滚到任意历史版本号 `VERSION`（本质是将旧版完整复制为一个新的顶层版本，历史记录完整保留，且零模型调用）：

```bash
tentacles --json persona rollback default 2 --expected-version 4 --note "回退到调整前的稳定画像"
```
