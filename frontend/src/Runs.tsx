import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, type Run } from './api'
import { useDisplayTime } from './display-time'
import { formatCost, formatDuration, formatStats } from './format'
import { Notice } from './shared'

export default function Runs() {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') || '', target = params.get('target') || ''
  const { formatTime, error: displayError } = useDisplayTime()
  const query = useQuery({ queryKey: ['runs', kind, target], queryFn: () => {
    const search = new URLSearchParams({ limit: '100' }); if (kind) search.set('kind', kind); if (target) search.set('target', target)
    return api<Run[]>(`/runs?${search}`)
  }, refetchInterval: 5000 })
  return <>
    <header className="page-heading"><div><p className="eyebrow">THE WORK BEHIND THE SIGNAL</p><h1>运行记录<span className="accent">.</span></h1><p className="muted">页面可见时每 5 秒自动刷新，未知费用不会被当作零。</p></div></header>
    <form className="filters" key={`${kind}/${target}`} onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); const search = new URLSearchParams(); for (const key of ['kind', 'target']) { const value = String(data.get(key) || '').trim(); if (value) search.set(key, value) }; setParams(search) }}><label>类型<select name="kind" defaultValue={kind}><option value="">全部类型</option>{['fetch', 'filter', 'pipeline', 'persona'].map(value => <option value={value} key={value}>{value}</option>)}{kind && !['fetch', 'filter', 'pipeline', 'persona'].includes(kind) && <option value={kind}>{kind}</option>}</select></label><label className="search-field">目标<input name="target" defaultValue={target} placeholder="信息源或 pipeline ID" /></label><button className="primary">筛选记录</button></form>
    {displayError && <Notice error>{displayError.message}</Notice>}{query.error && <Notice error>{query.error.message}</Notice>}{query.isPending && <Notice>正在读取运行记录…</Notice>}
    {query.data?.length === 0 && <div className="empty"><h2>没有匹配的运行记录</h2></div>}
    {!!query.data?.length && <div className="table-wrap"><table className="runs-table"><thead><tr><th>类型 / 目标</th><th>状态 / 时间</th><th>耗时 / 成本</th><th>统计 / 错误</th></tr></thead><tbody>{query.data.map(run => <tr key={run.id}><td><strong>{run.kind}</strong><div className="small">{run.target}</div><div className="muted small">#{run.id}</div></td><td><span className={`status ${['failed', 'error'].includes(run.status) ? 'bad' : ''}`}>{run.status}</span><div className="muted small">{formatTime(run.started_at)}</div></td><td className="tabular">{formatDuration(run.started_at, run.finished_at, run.status)}<div className="muted small">{formatCost(run.cost)}</div></td><td><p className="small">{formatStats(run.stats)}</p><details><summary>完整统计</summary><pre className="stats-json">{JSON.stringify(run.stats, null, 2)}</pre></details>{run.error && <p className="small error-text">{run.error}</p>}</td></tr>)}</tbody></table></div>}
  </>
}
