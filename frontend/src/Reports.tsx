import { useInfiniteQuery, useQueries, useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, safeUrl, type ItemDetail, type Pipeline, type Report, type ReportPage } from './api'
import { useDisplayTime } from './display-time'
import { formatCost } from './format'
import { Notice } from './shared'

function MarkdownReport({ markdown }: { markdown: string }) {
  return <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    a: ({ href, children }) => {
      const target = href?.startsWith('#') ? href : safeUrl(href || '')
      return target ? <a href={target} target={target.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a> : <span>{children}</span>
    },
    // Reports may contain private context. Never auto-load model-provided remote
    // images; keep an explicit, no-referrer link instead.
    img: ({ src, alt }) => safeUrl(typeof src === 'string' ? src : '') ? <a href={safeUrl(String(src))} target="_blank" rel="noopener noreferrer">图片：{alt || '在原站查看'}</a> : <span>{alt || '图片不可用'}</span>,
    table: ({ children }) => <div className="markdown-table"><table>{children}</table></div>,
  }}>{markdown}</Markdown></div>
}

function References({ ids }: { ids: string[] }) {
  const references = useQueries({ queries: ids.map(id => ({ queryKey: ['item', id], queryFn: () => api<ItemDetail>(`/items/${encodeURIComponent(id)}`) })) })
  return <section className="report-references"><h2>引用条目 <span className="muted small">{ids.length} 条</span></h2>{ids.length === 0 && <p className="muted">这份报告没有关联条目。</p>}<ol>{ids.map((id, index) => <li key={id}><Link to={`/feed?item=${encodeURIComponent(id)}`}>{references[index]?.data?.title || id}</Link><span className="muted small">{references[index]?.error ? ' 条目不可用' : references[index]?.data?.source_id ? ` · ${references[index].data.source_id}` : ' 加载中…'}</span></li>)}</ol></section>
}

export function ReportDetail() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const { formatTime, error: displayError } = useDisplayTime()
  const query = useQuery({ queryKey: ['report', id], queryFn: () => api<Report>(`/reports/${encodeURIComponent(id)}`) })
  const report = query.data
  return <>
    <Link className="inline-link small" to={`/reports?${params}`}>← 返回报告列表</Link>
    {query.isPending && <Notice>正在读取报告…</Notice>}{query.error && <Notice error>{query.error.message}</Notice>}{displayError && <Notice error>{displayError.message}</Notice>}
    {report && <><header className="reader-heading"><p className="eyebrow">REPORT / {report.kind.toUpperCase()}</p><h1>{report.title}</h1><p className="muted small">{report.pipeline_id} · {formatTime(report.created_at)} · {report.model || '未记录模型'} · 费用 {formatCost(report.cost)}</p></header><article className="report-paper"><MarkdownReport markdown={report.markdown} /></article><References ids={report.item_ids} /></>}
  </>
}

export default function Reports() {
  const [params, setParams] = useSearchParams()
  const pipeline = params.get('pipeline') || ''
  const { formatTime, error: displayError } = useDisplayTime()
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: () => api<Pipeline[]>('/pipelines') })
  const query = useInfiniteQuery({ queryKey: ['reports', pipeline], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => { const search = new URLSearchParams({ limit: '20', offset: String(pageParam) }); if (pipeline) search.set('pipeline', pipeline); return api<ReportPage>(`/reports?${search}`, { signal }) },
    getNextPageParam: page => page.offset + page.items.length < page.total ? page.offset + page.items.length : undefined,
  })
  const reports = [...new Map((query.data?.pages.flatMap(page => page.items) || []).map(report => [report.id, report])).values()]
  return <>
    <header className="page-heading"><div><p className="eyebrow">A RECORD OF WHAT MATTERS</p><h1>报告<span className="accent">.</span></h1><p className="muted">让信息留下结构，而不只是痕迹。</p></div><div className="total"><strong>{query.data?.pages[0]?.total ?? '—'}</strong><span>份报告</span></div></header>
    <label className="single-filter">处理流程<select value={pipeline} onChange={event => setParams(event.target.value ? { pipeline: event.target.value } : {})}><option value="">全部流程</option>{pipelines.data?.map(row => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}{pipeline && !pipelines.data?.some(row => row.id === pipeline) && <option value={pipeline}>{pipeline}</option>}</select></label>
    {displayError && <Notice error>{displayError.message}</Notice>}{query.error && <Notice error>{query.error.message}</Notice>}{pipelines.error && <Notice error>{pipelines.error.message}</Notice>}{query.isPending && <Notice>正在读取报告…</Notice>}
    {!query.isPending && !query.error && reports.length === 0 && <div className="empty"><h2>还没有报告</h2><p>前往「处理流程」运行一个有待处理条目的 pipeline。</p></div>}
    <div className="feed-list">{reports.map(report => <article className="item-card" key={report.id}><div className="item-meta"><span className="tag">{report.kind}</span>{report.pipeline_id}<span>·</span>{formatTime(report.created_at)}</div><h2><Link to={`/reports/${encodeURIComponent(report.id)}?${params}`}>{report.title}</Link></h2><div className="card-footer"><span className="muted small">{report.item_ids.length} 条引用 · {formatCost(report.cost)}</span><Link className="read-link" to={`/reports/${encodeURIComponent(report.id)}?${params}`}>阅读报告 ↗</Link></div></article>)}</div>
    {query.hasNextPage && <button className="load-more" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? '正在加载…' : '加载更多 ↓'}</button>}
  </>
}
