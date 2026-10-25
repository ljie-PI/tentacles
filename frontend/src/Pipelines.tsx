import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { useDisplayTime } from './display-time'
import { Notice } from './shared'

export default function Pipelines() {
  const client = useQueryClient()
  const { formatTime, error: displayError } = useDisplayTime()
  const query = useQuery({ queryKey: ['pipelines'], queryFn: () => api('/api/pipelines', 'get'), refetchInterval: 15000 })
  const action = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: 'filter' | 'run' }) => kind === 'filter'
      ? api('/api/pipelines/{pipeline_id}/filter', 'post', { path: { pipeline_id: id } })
      : api('/api/pipelines/{pipeline_id}/run', 'post', { path: { pipeline_id: id } }),
    onSettled: async () => {
      await Promise.all(['pipelines', 'items', 'item', 'reports', 'runs', 'scheduler'].map(key => client.invalidateQueries({ queryKey: [key] })))
    },
  })
  const result = action.data
  const reportIds = result && Array.isArray(result.stats?.report_ids)
    ? result.stats.report_ids.filter((id): id is string => typeof id === 'string') : result?.report_id ? [result.report_id] : []
  return <>
    <header className="page-heading"><div><p className="eyebrow">FROM SIGNAL TO INSIGHT</p><h1>处理流程<span className="accent">.</span></h1><p className="muted">按自己的节奏，筛选、整理，再深入一点。</p></div></header>
    <p className="section-note">手动过滤／运行也可触发停用的 pipeline，可能调用付费模型。配置通过 YAML 管理。</p>
    {displayError && <Notice error>{displayError.message}</Notice>}
    {query.isPending && <Notice>正在读取处理流程…</Notice>}
    {query.error && <Notice error>{query.error.message}</Notice>}
    {action.error && <Notice error>{action.error.message}，可在运行记录中查看详情。</Notice>}
    {result && <Notice error={['error', 'busy'].includes(result.status)}>{result.pipeline_id}：{result.status === 'no_new' ? '没有新的待处理条目。' : result.status === 'ok' ? '操作已完成。' : result.error || result.status}{reportIds.map(id => <Link className="inline-link" key={id} to={`/reports/${encodeURIComponent(id)}`}>阅读报告 ↗</Link>)}</Notice>}
    {query.data?.length === 0 && <div className="empty"><h2>尚未配置处理流程</h2><p>在 YAML 的 pipelines 中添加后重启服务。</p></div>}
    <div className="pipeline-grid">{query.data?.map(pipeline => <section className="pipeline-card" key={pipeline.id}>
      <div className="card-heading"><h2>{pipeline.name || pipeline.id}</h2><span className="tag">{pipeline.enabled ? '已启用' : '手动'}</span></div>
      <p className="muted small">{pipeline.id} · Persona {pipeline.persona}</p>
      <dl className="config-summary"><div><dt>处理方式</dt><dd>{pipeline.processor}</dd></div><div><dt>运行计划</dt><dd>{pipeline.schedule}</dd></div><div><dt>下次运行</dt><dd>{formatTime(pipeline.next_run_at)}</dd></div><div><dt>待报告</dt><dd>{pipeline.pending} 条</dd></div><div><dt>只过滤计划</dt><dd>{pipeline.filter_schedule || '未设置'} · {formatTime(pipeline.filter_next_run_at)}</dd></div><div><dt>信息源</dt><dd>{pipeline.sources.join('、')}</dd></div></dl>
      {pipeline.last_error && <p className="small error-text">{pipeline.last_error}</p>}
      <div className="pipeline-actions"><button disabled={action.isPending} onClick={() => action.mutate({ id: pipeline.id, kind: 'filter' })}>只过滤</button><button className="primary" disabled={action.isPending} onClick={() => action.mutate({ id: pipeline.id, kind: 'run' })}>{action.isPending && action.variables?.id === pipeline.id ? '处理中…' : '运行流程'}</button><Link className="small inline-link" to={`/feed?pipeline=${encodeURIComponent(pipeline.id)}`}>查看信息流 ↗</Link></div>
    </section>)}</div>
  </>
}
