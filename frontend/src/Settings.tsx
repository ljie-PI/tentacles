import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api, getToken, saveToken, type ModelTest } from './api'
import { useDisplayTime } from './display-time'
import { formatCost } from './format'
import { Notice } from './shared'

function Models() {
  const query = useQuery({ queryKey: ['models'], queryFn: () => api('/api/models', 'get') })
  const [results, setResults] = useState<Record<string, ModelTest>>({})
  const probe = useMutation({ mutationFn: (name: string) => api('/api/models/{name}/test', 'post', { path: { name } }),
    onSuccess: (result, name) => setResults(current => ({ ...current, [name]: result })) })
  return <section className="settings-section"><h2>模型连通性</h2><p className="section-note">只有点击「测试模型」才发出请求，测试也可能产生费用。模型和密钥通过 YAML／环境变量管理。</p>
    {query.isPending && <Notice>正在读取模型…</Notice>}{query.error && <Notice error>{query.error.message}</Notice>}{probe.error && <Notice error>{probe.error.message}</Notice>}
    {query.data?.length === 0 && <p className="muted">没有配置模型。</p>}
    {!!query.data?.length && <div className="table-wrap"><table><thead><tr><th>模型</th><th>测试结果</th><th>操作</th></tr></thead><tbody>{query.data.map(model => { const result = results[model.name]; return <tr key={model.name}><td><strong>{model.name}</strong><div className="small muted">{model.provider} · {model.kind}</div><div className="small">{model.model}</div><div className="small muted">{model.key_configured ? '密钥已配置' : '未配置密钥'}</div></td><td>{result ? <><span className={`status ${result.ok ? '' : 'bad'}`}>{result.ok ? '可用' : '不可用'}</span><div className="small muted">{Math.round(result.latency_ms)}ms · {formatCost(result.cost)}</div>{result.error && <p className="small error-text">{result.error}</p>}</> : <span className="muted small">尚未测试</span>}</td><td><button disabled={probe.isPending} onClick={() => probe.mutate(model.name)}>{probe.isPending && probe.variables === model.name ? '测试中…' : '测试模型'}</button></td></tr> })}</tbody></table></div>}
  </section>
}

function Scheduler() {
  const client = useQueryClient()
  const { formatTime, error: displayError } = useDisplayTime()
  const tasks = useQuery({ queryKey: ['scheduler', 'tasks'], queryFn: () => api('/api/scheduler', 'get'), refetchInterval: 5000 })
  const loop = useQuery({ queryKey: ['scheduler', 'loop'], queryFn: () => api('/api/scheduler/loop', 'get'), refetchInterval: 5000 })
  const tick = useMutation({ mutationFn: () => api('/api/scheduler/tick', 'post'), onSettled: async () => {
    await Promise.all(['scheduler', 'sources', 'pipelines', 'items', 'item', 'reports', 'runs'].map(key => client.invalidateQueries({ queryKey: [key] })))
  } })
  return <section className="settings-section"><div className="card-heading"><h2>调度状态</h2><button disabled={tick.isPending} onClick={() => tick.mutate()}>{tick.isPending ? '执行中…' : '执行一次到期任务'}</button></div><p className="section-note">每 5 秒刷新。手动执行可能抓取信息源或运行 pipeline，并调用付费模型；只处理已启用且到期的任务。</p>
    {loop.data && <p className="small muted">后台轮询{loop.data.enabled ? '已启用' : '已停用'} · 间隔 {loop.data.poll_interval}s · 已执行 {loop.data.cycles ?? 0} 轮，失败 {loop.data.failed_cycles ?? 0} 轮 · 上轮 {formatTime(loop.data.last_tick_at)}</p>}
    {loop.data?.last_error && <Notice error>{loop.data.last_error}</Notice>}{displayError && <Notice error>{displayError.message}</Notice>}{tasks.error && <Notice error>{tasks.error.message}</Notice>}{loop.error && <Notice error>{loop.error.message}</Notice>}{tick.error && <Notice error>{tick.error.message}</Notice>}
    {tick.data && <Notice>{tick.data.length === 0 ? '没有到期任务。' : '到期任务已返回结果：'}{tick.data.map((result, index) => <div key={`${result.kind}-${result.target}-${index}`} className={['error', 'busy'].includes(result.status) ? 'error-text' : ''}>{result.kind} / {result.target}：{result.status}{result.error && ` · ${result.error}`}{result.report_id && <Link className="inline-link" to={`/reports/${encodeURIComponent(result.report_id)}`}>阅读报告 ↗</Link>}</div>)}</Notice>}
    {tasks.isPending && <Notice>正在读取任务…</Notice>}{tasks.data?.length === 0 && <p className="muted">没有配置任务。</p>}
    {!!tasks.data?.length && <div className="table-wrap"><table><thead><tr><th>类型 / 目标</th><th>计划 / 状态</th><th>下次运行</th></tr></thead><tbody>{tasks.data.map(task => <tr key={`${task.kind}-${task.target}`}><td><strong>{task.target}</strong><div className="small muted">{task.kind}</div></td><td>{task.schedule}<div className="small muted">{task.running ? '运行中' : task.enabled ? '待调度' : '已停用'} · {task.last_status || '尚未运行'}</div>{task.last_error && <p className="small error-text">{task.last_error}</p>}</td><td className="tabular">{formatTime(task.next_run_at)}</td></tr>)}</tbody></table></div>}
  </section>
}

export default function Settings() {
  const [params] = useSearchParams()
  const client = useQueryClient()
  return <>
    <header className="page-heading"><div><p className="eyebrow">MAKE YOURSELF AT HOME</p><h1>设置<span className="accent">.</span></h1><p className="muted">连接你的私人信息台。</p></div></header>
    {params.get('auth') === 'required' && <Notice error>服务器要求有效的访问令牌，请在下方设置后重试。</Notice>}
    <section className="settings-panel"><h2>访问令牌</h2><p className="muted">与服务器配置的 server.token 一致。仅保存在当前浏览器，自动附加到 API 请求；未启用鉴权时可留空。</p>
      <form onSubmit={event => {
        event.preventDefault(); const data = new FormData(event.currentTarget)
        saveToken(String(data.get('token') || '').trim()); client.clear()
        // Remount all observers and local probe results after a credential
        // change; a same-page reload prevents old data surviving cache.clear().
        window.location.replace('/settings?saved=1')
      }}><label>Bearer token<input name="token" type="password" autoComplete="off" defaultValue={getToken()} placeholder="输入访问令牌" /></label><button className="primary" type="submit">保存令牌</button></form>
      {params.get('saved') === '1' && <Notice>令牌已保存，页面已使用新令牌重新连接。</Notice>}
      <p className="small muted">请勿在不受信任的设备保存令牌。跨设备或对外访问请通过 HTTPS 反向代理。</p>
    </section><Models /><Scheduler />
  </>
}
