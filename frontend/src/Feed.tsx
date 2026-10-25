import { useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, safeUrl, type Item, type ItemDetail, type ItemPage, type Source, type Pipeline } from './api'
import { Drawer, Notice, ScoreBadge } from './shared'
import { useDisplayTime, type TimeFormatter } from './display-time'

function ItemCard({ item, open, formatTime }: { item: Item; open: () => void; formatTime: TimeFormatter }) {
  return <article className="item-card">
    <div className="item-meta"><span className="source-dot" />{item.source_id}<span>·</span>{formatTime(item.published || item.fetched_at)}<span className="kind">{item.kind}</span></div>
    <h2><button className="text-button item-title" onClick={open}>{item.title}</button></h2>
    <p className="summary">{item.summary || '暂无摘要，打开详情阅读正文。'}</p>
    <footer className="card-footer"><div className="tags">{item.score != null && <ScoreBadge score={item.score} />}{(item.tags || []).map(tag => <span className="tag" key={tag}>{tag}</span>)}{item.stage && <span className="tag">阶段：{item.stage}</span>}{item.passed != null && <span className="tag">{item.passed ? '通过' : '未通过'}</span>}</div><button className="text-button read-link" onClick={open}>阅读详情 <span aria-hidden="true">↗</span></button></footer>
    {item.reason && <p className="muted small">{item.reason}</p>}
  </article>
}

function Detail({ id, close }: { id: string; close: () => void }) {
  const { formatTime } = useDisplayTime()
  const query = useQuery({ queryKey: ['item', id], queryFn: () => api<ItemDetail>(`/items/${encodeURIComponent(id)}`) })
  const item = query.data
  return <Drawer title={item?.title || '加载条目…'} close={close}>
    {query.isPending && <Notice>正在读取正文…</Notice>}
    {query.error && <Notice error>{query.error.message}</Notice>}
    {item && <>
      <p className="muted">{item.source_id} · {formatTime(item.published || item.fetched_at)}</p>
      {safeUrl(item.url) && <a className="external-link" href={safeUrl(item.url)} target="_blank" rel="noopener noreferrer">在原站阅读 ↗</a>}
      <div className="item-content">{item.content || item.summary || '此条目没有正文。'}</div>
      {(item.evaluations || []).length > 0 && <section className="evaluations"><h3>Pipeline 评估</h3>{(item.evaluations || []).map(e => <div key={`${e.pipeline_id}-${e.persona_version}`}><strong>{e.pipeline_id}</strong><ScoreBadge score={e.score} /><span className="tag">{e.stage} · {e.passed ? '通过' : '未通过'}</span><p>{e.reason}</p></div>)}</section>}
    </>}
  </Drawer>
}

type Filters = { source: string; since: string; search: string; pipeline: string; passed: string }

function FeedFilters({ filters, sources, pipelines, apply }: { filters: Filters; sources: Source[] | undefined; pipelines: Pipeline[] | undefined; apply: (query: URLSearchParams) => void }) {
  const [draft, setDraft] = useState(filters)
  return <form className="filters" onSubmit={event => {
    event.preventDefault()
    const clean = { source: draft.source.trim(), since: draft.since.trim(), search: draft.search.trim(), pipeline: draft.pipeline, passed: draft.pipeline ? draft.passed : '' }
    const next = new URLSearchParams()
    for (const [key, value] of Object.entries(clean)) if (value) next.set(key, value)
    setDraft(clean); apply(next)
  }}>
    <label>信息源<select name="source" value={draft.source} onChange={event => setDraft({ ...draft, source: event.target.value })}><option value="">全部来源</option>{sources?.map(row => <option value={row.id} key={row.id}>{row.name || row.id}</option>)}{draft.source && !sources?.some(row => row.id === draft.source) && <option value={draft.source}>{draft.source}</option>}</select></label>
    <label>时间范围<select name="since" value={draft.since} onChange={event => setDraft({ ...draft, since: event.target.value })}><option value="">全部时间</option><option value="1d">最近 24 小时</option><option value="7d">最近 7 天</option><option value="30d">最近 30 天</option>{draft.since && !['1d', '7d', '30d'].includes(draft.since) && <option value={draft.since}>{draft.since}</option>}</select></label>
    <label>处理流程<select name="pipeline" value={draft.pipeline} onChange={event => setDraft({ ...draft, pipeline: event.target.value, passed: event.target.value ? draft.passed : '' })}><option value="">全部条目</option>{pipelines?.map(row => <option value={row.id} key={row.id}>{row.name || row.id}</option>)}{draft.pipeline && !pipelines?.some(row => row.id === draft.pipeline) && <option value={draft.pipeline}>{draft.pipeline}</option>}</select></label>
    <label>判定<select name="passed" value={draft.passed} disabled={!draft.pipeline} onChange={event => setDraft({ ...draft, passed: event.target.value })}><option value="">全部判定</option><option value="true">仅通过</option><option value="false">未通过</option>{draft.passed && !['true', 'false'].includes(draft.passed) && <option value={draft.passed}>{draft.passed}</option>}</select></label>
    <label className="search-field">关键词<input name="search" value={draft.search} onChange={event => setDraft({ ...draft, search: event.target.value })} placeholder="搜索标题与正文…" type="search" /></label><button className="primary" type="submit">筛选内容</button>
  </form>
}

export default function Feed() {
  const display = useDisplayTime()
  const [params, setParams] = useSearchParams()
  const source = params.get('source') || '', since = params.get('since') || '', search = params.get('search') || ''
  const pipeline = params.get('pipeline') || '', passed = params.get('passed') || ''
  const selected = params.get('item')
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => api<Source[]>('/sources') })
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: () => api<Pipeline[]>('/pipelines') })
  const feed = useInfiniteQuery({
    queryKey: ['items', source, since, search, pipeline, passed], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => {
      const query = new URLSearchParams({ limit: '20', offset: String(pageParam) })
      if (source) query.set('sources', source)
      if (since) query.set('since', since)
      if (search) query.set('search', search)
      if (pipeline) query.set('pipeline', pipeline)
      if (passed) query.set('passed', passed)
      return api<ItemPage>(`/items?${query}`, { signal })
    },
    getNextPageParam: last => last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined,
  })
  const items = [...new Map((feed.data?.pages.flatMap(page => page.items) || []).map(item => [item.id, item])).values()]
  function select(id?: string) {
    setParams(current => { const next = new URLSearchParams(current); if (id) next.set('item', id); else next.delete('item'); return next })
  }
  return <>
    <header className="page-heading"><div><p className="eyebrow">YOUR SIGNAL, NOT THE NOISE</p><h1>信息流<span className="accent">.</span></h1><p className="muted">把分散的线索，汇聚在一个安静的地方。</p></div><div className="total"><strong>{feed.data?.pages[0]?.total ?? '—'}</strong><span>条匹配内容</span></div></header>
    <FeedFilters key={JSON.stringify([source, since, search, pipeline, passed])} filters={{ source, since, search, pipeline, passed }} sources={sources.data} pipelines={pipelines.data} apply={setParams} />
    {display.error && <Notice error>{display.error.message}</Notice>}
    {feed.error && <Notice error>{feed.error.message} <button onClick={() => void feed.refetch()}>重试</button></Notice>}
    {pipelines.error && <Notice error>处理流程列表加载失败：{pipelines.error.message}</Notice>}
    {sources.error && <Notice error>信息源列表加载失败：{sources.error.message}</Notice>}
    {feed.isPending && <Notice>正在汇聚信息…</Notice>}
    {!feed.isPending && !feed.error && items.length === 0 && <div className="empty"><span aria-hidden="true">◎</span><h2>这里还很安静</h2><p>试着调整筛选条件，或前往「信息源」立即抓取。</p></div>}
    <div className="feed-list">{items.map(item => <ItemCard item={item} key={item.id} formatTime={display.formatTime} open={() => select(item.id)} />)}</div>
    {feed.hasNextPage && <button className="load-more" disabled={feed.isFetchingNextPage} onClick={() => void feed.fetchNextPage()}>{feed.isFetchingNextPage ? '正在加载…' : '加载更多 ↓'}</button>}
    {selected && <Detail id={selected} close={() => select()} />}
  </>
}
