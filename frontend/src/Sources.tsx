import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type FetchOutcome, type Source } from './api'
import { Notice } from './shared'
import { useDisplayTime } from './display-time'

export default function Sources() {
  const { formatTime, error: displayError } = useDisplayTime()
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['sources'], queryFn: () => api<Source[]>('/sources'), refetchInterval: 30000 })
  const fetch = useMutation({
    mutationFn: (id: string) => api<FetchOutcome>(`/sources/${encodeURIComponent(id)}/fetch`, { method: 'POST' }),
    onSettled: async () => {
      await Promise.all(['sources', 'items', 'runs', 'scheduler'].map(key => client.invalidateQueries({ queryKey: [key] })))
    },
  })
  return <>
    <header className="page-heading"><div><p className="eyebrow">CONNECTED TO YOUR WORLD</p><h1>信息源<span className="accent">.</span></h1><p className="muted">每一条信息，都有迹可循。</p></div><div className="total"><strong>{query.data?.length ?? '—'}</strong><span>个已配置来源</span></div></header>
    <p className="section-note">配置由 YAML 管理。手动抓取也可运行停用的信息源；网页 LLM 源可能产生费用。</p>
    {displayError && <Notice error>{displayError.message}</Notice>}
    {query.error && <Notice error>{query.error.message} <button onClick={() => void query.refetch()}>重试</button></Notice>}
    {query.isPending && <Notice>正在读取信息源…</Notice>}
    {fetch.error && <Notice error>{fetch.error.message}</Notice>}
    {fetch.data && !fetch.isPending && <Notice>抓取完成：新增 {fetch.data.new} 条，重复 {fetch.data.dup} 条。</Notice>}
    {query.data && query.data.length === 0 && <div className="empty"><h2>尚未配置来源</h2><p>在 YAML 的 sources 中添加 RSS、邮箱或网页源后重启。</p></div>}
    {!!query.data?.length && <div className="table-wrap"><table><thead><tr><th>来源 / 类型</th><th>最近状态</th><th>条目</th><th>下次运行</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{query.data.map(source => <tr key={source.id}>
      <td><strong>{source.name || source.id}</strong><div className="muted small">{source.type.toUpperCase()} · {source.schedule} {!source.enabled && '· 已停用'}</div></td>
      <td><span className={`status ${source.last_status === 'error' ? 'bad' : ''}`}>{source.last_status === 'ok' ? '正常' : source.last_status === 'error' ? '异常' : '尚未抓取'}</span><div className="muted small">{formatTime(source.last_fetch_at)}</div>{source.last_error && <div className="small error-text">{source.last_error}</div>}</td>
      <td className="tabular">{source.total_items}</td><td className="tabular">{formatTime(source.next_run_at)}</td>
      <td><button disabled={fetch.isPending} onClick={() => fetch.mutate(source.id)}>{fetch.isPending && fetch.variables === source.id ? '抓取中…' : '立即抓取'}</button></td>
    </tr>)}</tbody></table></div>}
  </>
}
