# 触发采集、流水线与运维状态速查

除读取已入库数据与管理 Persona 外，拥有运维权限的 Agent 还可以通过以下 `--json` 命令触发抓取、运行过滤/报告流水线，并审计运行状态与模型账单。

---

## 1. 信息源列表与手动抓取

```bash
# 列出所有配置的信息源、上次抓取状态、累计条数与下次调度时间
tentacles --json sources list

# 立即并发抓取所有已启用（enabled: true）的信息源
tentacles --json sources fetch

# 显式指定一个或多个源 ID 立即抓取（即使配置中 enabled: false 也会执行）
tentacles --json sources fetch hacker-news inbox
```

### 返回结果检查要点（`FetchOutcome[]`）

```json
[
  {
    "source_id": "hacker-news",
    "run_id": 7,
    "status": "ok",
    "new": 5,
    "updated": 2,
    "dup": 13,
    "error": null,
    "stats": {"entries": 20, "too_old": 0, "fetched": 20, "new": 5, "updated": 2, "dup": 13}
  },
  {
    "source_id": "broken-feed",
    "run_id": 8,
    "status": "error",
    "new": 0,
    "updated": 0,
    "dup": 0,
    "error": "RSS request returned HTTP 500",
    "stats": {}
  }
]
```

- **必须逐项检查 `status`**：批量抓取中即使部分源报错（`status: "error"`）或正被其他进程占用锁（`status: "busy"`，`run_id: 0`），**进程退出码仍为 `0`**；仅当传入了不存在的 Source ID 时才返回退出码 `3`（`not_found`）。
- 成功批次满足 `stats.fetched = new + updated + dup`（未开启 `update_existing: true` 的源 `updated` 恒为 `0`）。

---

## 2. 流水线查询、仅过滤与生成报告

```bash
# 查看所有 Pipeline 配置、待报告候选数（pending）与下次运行时间
tentacles --json pipelines list

# 仅对窗口内尚未被当前 Persona 版本评估的条目执行过滤打分（不生成报告）
tentacles --json pipelines filter daily-tech

# 清除最近 7 天当前 Persona 版本的评估缓存并强制重评
tentacles --json pipelines filter daily-tech --reevaluate --since 7d

# 先执行过滤，再选取高分未消费条目调用处理器生成报告（可能产生模型费用）
tentacles --json pipelines run daily-tech
```

### `pipelines run` 返回解读（`PipelineOutcome`）

```json
{
  "pipeline_id": "daily-tech",
  "run_id": 12,
  "status": "ok",
  "report_id": "20261025-daily-tech-a1b2c3d4",
  "stats": {
    "candidates": 18,
    "evaluated": 18,
    "passed": 6,
    "rejected": 12,
    "errors": 0,
    "filter_status": "ok",
    "filter_run_id": 11,
    "processor_candidates": 6,
    "consumed": 6,
    "report_ids": ["20261025-daily-tech-a1b2c3d4"],
    "usage": {"input_tokens": 3500, "output_tokens": 900, "total_tokens": 4400},
    "cost": 0.024,
    "models": ["deepseek-flash", "claude-sonnet-5-5"]
  },
  "error": null
}
```

- `status` 取值：`ok`、`no_new`（窗口内没有新的待消费通过条目，`report_id` 为 `null`）、`busy`（锁被占用）、`error`（单次运行失败，**退出码仍为 `0`**）。
- 对于一次可能产出多篇报告的 `deep` 处理器，`report_id` 为第一篇报告 ID，`stats.report_ids` 包含本批生成的全部报告 ID。

---

## 3. 周期调度器与单轮触发（`scheduler status` & `tick`）

```bash
# 查看全部 fetch / filter / pipeline 任务的启用状态、锁状态、下次触发时间与 pending 数
tentacles --json scheduler status

# 按严格顺序执行所有已启用且已到期的任务（1.并发抓源 -> 2.过滤 -> 3.跑报告）
tentacles --json tick
```

- 无任何到期任务时，`tick` 返回空数组 `[]`。
- 若某个到期源在阶段 1 返回 `status: "busy"`，依赖该源的所有下游 `filter` 和 `pipeline` 任务本轮会自动跳过且不推进调度时间，防止漏抓漏报。

---

## 4. 配置校验、模型探测与计费运行记录

```bash
# 无副作用校验 YAML 配置、环境变量引用、模板语法与 Persona 引用（失败时退出码为 2）
tentacles --json config check

# 列出命名模型及密钥是否已配置（不联网、不泄露密钥）
tentacles --json models list

# 实际发起一次探测调用验证模型可用性与延迟（退出码为 0，检查返回中的 ok 布尔值）
tentacles --json models test fast

# 查询最近的运行与计费记录（可选按 --kind fetch|filter|pipeline|persona 和 --target 过滤）
tentacles --json runs list --limit 20
tentacles --json runs list --kind pipeline --target daily-tech
```

### 费用汇总防重提示（重要）
每次执行 `pipelines run` 时，内部触发的过滤子步骤会单独产生一条 `kind: "filter"` 的子运行记录（其 ID 保存在父 `pipeline` 运行记录的 `stats.filter_run_id` 中），而父 `pipeline` 记录的 `usage` 与 `cost` **已经合并包含了该子过滤步骤的开销**。因此 Agent 在统计总账单时，**请勿将父 `pipeline` Run 与它关联的 `stats.filter_run_id` 子 Run 重复相加**；同样，单次 `sources fetch` 返回结果中的 `stats.cost` 就是其对应 `run_id` 记录的同笔费用。

---

## 5. 失败诊断与错误排查

- 检查 `sources list` / `pipelines list` / `scheduler status` 中的 `last_status` 与 `last_error`；
- 调用 `tentacles --json runs list --kind fetch --target <SOURCE_ID>` 查看失败运行的 `error` 摘要与 `stats`（如 `dropped_links`、阶段 `errors` 计数、`aborted` / `abort_status`）；
- 对单条已入库条目的过滤阶段异常，可通过 `tentacles --json items show <ITEM_ID>` 查看 `evaluations[].stage_scores[].error`。
