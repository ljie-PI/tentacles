import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, type ErrorEvent } from './api'
import { useDisplayTime, type TimeFormatter } from './display-time'
import { formatCost, formatDuration, formatStats } from './format'
import { Drawer, Notice } from './shared'

// The list row carries no debug context; it is fetched per error only when the
// reader opens it, so a run with many failures stays cheap to inspect.
function ErrorEntry({ error, formatTime }: { error: ErrorEvent; formatTime: TimeFormatter }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['error', error.id], enabled: open, queryFn: () =>
    api('/api/errors/{error_id}', 'get', { path: { error_id: error.id } }) })
  return <article className="run-error">
    <div className="run-error-head"><strong>#{error.id}</strong><span className="tag">{error.error_code}</span><span className="tag">{error.phase}</span><span className="tag">{error.retryable ? '可重试' : '需改配置'}</span>{error.resolved_at ? <span className="tag">已消警 {formatTime(error.resolved_at)}</span> : <span className="tag bad">未消警</span>}</div>
    <p className="small error-text">{error.message}</p>
    <p className="small muted">{error.target_url || error.external_id || error.page_url || '没有定位到具体目标'}{error.http_status != null && ` · HTTP ${error.http_status}`}{error.exception_type && ` · ${error.exception_type}`}</p>
    <button className="text-button read-link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '收起排障现场' : '查看排障现场'} <span aria-hidden="true">{open ? '↑' : '↗'}</span></button>
    {open && query.isPending && <Notice>正在读取排障现场…</Notice>}
    {open && query.error && <Notice error>{query.error.message}</Notice>}
    {open && query.data && <>
      <p className="small muted">来源 {query.data.source_id || '—'} · 流程 {query.data.pipeline_id || '—'} · 记录于 {formatTime(query.data.occurred_at)}{query.data.item_id && ` · 条目 ${query.data.item_id}`}</p>
      <pre className="stats-json debug-context">{JSON.stringify(query.data.debug_context, null, 2)}</pre>
    </>}
  </article>
}

function RunDetail({ runId, close }: { runId: number; close: () => void }) {
  const { formatTime } = useDisplayTime()
  const query = useQuery({ queryKey: ['run', runId], queryFn: () =>
    api('/api/runs/{run_id}', 'get', { path: { run_id: runId } }) })
  const run = query.data
  return <Drawer eyebrow="运行详情" title={`运行 #${runId}`} close={close}>
    {query.isPending && <Notice>正在读取运行详情…</Notice>}
    {query.error && <Notice error>{query.error.message}</Notice>}
    {run && <>
      <p className="muted">{run.kind} · {run.target} · <span className={`status ${['failed', 'error'].includes(run.status) ? 'bad' : ''}`}>{run.status}</span></p>
      <p className="muted small">开始 {formatTime(run.started_at)} · 结束 {formatTime(run.finished_at)} · 耗时 {formatDuration(run.started_at, run.finished_at, run.status)} · 成本 {formatCost(run.cost)}</p>
      {run.error && <Notice error>{run.error}</Notice>}
      <h3>故障账本（{(run.errors || []).length}）</h3>
      <p className="section-note">业务写入回滚不会丢记录；目标在后续重试中成功获取或评估后会自动消警。</p>
      {!(run.errors || []).length && <p className="muted small">这次运行没有记录故障事件。</p>}
      {(run.errors || []).map(error => <ErrorEntry key={error.id} error={error} formatTime={formatTime} />)}
      <details><summary>完整统计</summary><pre className="stats-json">{JSON.stringify(run.stats, null, 2)}</pre></details>
    </>}
  </Drawer>
}

export default function Runs() {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') || '', target = params.get('target') || ''
  const opened = Number(params.get('run')) || null
  const { formatTime, error: displayError } = useDisplayTime()
  const query = useQuery({ queryKey: ['runs', kind, target], queryFn: () =>
    api('/api/runs', 'get', { query: { limit: 100, kind: kind || undefined, target: target || undefined } }),
  refetchInterval: 5000 })
  const open = (runId?: number) => setParams(current => {
    const next = new URLSearchParams(current)
    if (runId) next.set('run', String(runId)); else next.delete('run')
    return next
  })
  return <>
    <header className="page-heading"><div><p className="eyebrow">THE WORK BEHIND THE SIGNAL</p><h1>运行记录<span className="accent">.</span></h1><p className="muted">页面可见时每 5 秒自动刷新，未知费用不会被当作零。点开单次运行可查看故障账本与排障现场。</p></div></header>
    <form className="filters" key={`${kind}/${target}`} onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); const search = new URLSearchParams(); for (const key of ['kind', 'target']) { const value = String(data.get(key) || '').trim(); if (value) search.set(key, value) }; setParams(search) }}><label>类型<select name="kind" defaultValue={kind}><option value="">全部类型</option>{['fetch', 'filter', 'pipeline', 'persona'].map(value => <option value={value} key={value}>{value}</option>)}{kind && !['fetch', 'filter', 'pipeline', 'persona'].includes(kind) && <option value={kind}>{kind}</option>}</select></label><label className="search-field">目标<input name="target" defaultValue={target} placeholder="信息源或 pipeline ID" /></label><button className="primary">筛选记录</button></form>
    {displayError && <Notice error>{displayError.message}</Notice>}{query.error && <Notice error>{query.error.message}</Notice>}{query.isPending && <Notice>正在读取运行记录…</Notice>}
    {query.data?.length === 0 && <div className="empty"><h2>没有匹配的运行记录</h2></div>}
    {!!query.data?.length && <div className="table-wrap"><table className="runs-table"><thead><tr><th>类型 / 目标</th><th>状态 / 时间</th><th>耗时 / 成本</th><th>统计 / 错误</th></tr></thead><tbody>{query.data.map(run => <tr key={run.id}><td><strong>{run.kind}</strong><div className="small">{run.target}</div><div className="muted small">#{run.id}</div></td><td><span className={`status ${['failed', 'error'].includes(run.status) ? 'bad' : ''}`}>{run.status}</span><div className="muted small">{formatTime(run.started_at)}</div></td><td className="tabular">{formatDuration(run.started_at, run.finished_at, run.status)}<div className="muted small">{formatCost(run.cost)}</div></td><td><p className="small">{formatStats(run.stats)}</p><details><summary>完整统计</summary><pre className="stats-json">{JSON.stringify(run.stats, null, 2)}</pre></details>{run.error && <p className="small error-text">{run.error}</p>}<button className="text-button read-link" onClick={() => open(run.id)}>故障详情 <span aria-hidden="true">↗</span></button></td></tr>)}</tbody></table></div>}
    {opened != null && <RunDetail runId={opened} close={() => open()} />}
  </>
}
