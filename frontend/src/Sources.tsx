import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type SourceConfig, type SourceCreate, type SourceUpdate } from './api'
import { Drawer, Notice } from './shared'
import { useDisplayTime } from './display-time'

type SourceType = 'rss' | 'web' | 'browser' | 'imap'
type CreatableSourceType = Exclude<SourceType, 'imap'>
type WebMode = 'list' | 'page' | 'llm'
type BrowserMode = 'script' | 'list'
type BrowserPreset = '' | 'reddit' | 'x-following'
type DetailErrorMode = 'fail_batch' | 'record_and_skip'

type SourceDraft = {
  id: string
  type: SourceType
  name: string
  schedule: string
  enabled: boolean
  updateExisting: boolean
  url: string
  maxAgeDays: string
  timeout: string
  mode: WebMode
  browserMode: BrowserMode
  browserPreset: BrowserPreset
  browserMaxScrolls: string
  selItem: string
  selTitle: string
  selLink: string
  selSummary: string
  selDate: string
  selector: string
  fetchContent: boolean
  onDetailError: DetailErrorMode
  emitBaseline: boolean
  maxInputChars: string
  model: string
  concurrency: string
  pageUrl: string
  task: string
  chromeProfile: string
  maxItems: string
  host: string
  port: string
  ssl: boolean
  folders: string
  includeSubfolders: boolean
  initialDays: string
  maxFetch: string
  search: string
  excludeSenders: string
  pipelines: string[]
}

function emptyDraft(): SourceDraft {
  return {
    id: '', type: 'rss', name: '', schedule: '4h', enabled: true, updateExisting: false,
    url: '', maxAgeDays: '', timeout: '',
    mode: 'list', browserMode: 'script', browserPreset: 'reddit', browserMaxScrolls: '20',
    selItem: 'article', selTitle: 'h2', selLink: 'a@href', selSummary: '', selDate: '',
    selector: 'main', fetchContent: false, onDetailError: 'fail_batch', emitBaseline: true,
    maxInputChars: '20000', model: '', concurrency: '4',
    pageUrl: '', task: '', chromeProfile: '', maxItems: '25',
    host: 'imap.gmail.com', port: '', ssl: true, folders: 'INBOX', includeSubfolders: false,
    initialDays: '30', maxFetch: '100', search: 'ALL', excludeSenders: '',
    pipelines: [],
  }
}

const selectorStr = (selectors: SourceConfig['selectors'], key: string): string => {
  const val = selectors?.[key]
  return typeof val === 'string' ? val : ''
}

function draftFromConfig(cfg: SourceConfig): SourceDraft {
  const webMode: WebMode = cfg.mode === 'page' || cfg.mode === 'llm' ? cfg.mode : 'list'
  const browserMode: BrowserMode = cfg.mode === 'list' ? 'list' : 'script'
  const rawMaxScrolls = cfg.scroll && typeof cfg.scroll.max_scrolls === 'number'
    ? String(cfg.scroll.max_scrolls)
    : ''
  return {
    id: cfg.id,
    type: cfg.type,
    name: cfg.name || '',
    schedule: cfg.schedule,
    enabled: cfg.enabled,
    updateExisting: cfg.update_existing ?? false,
    url: cfg.url || '',
    maxAgeDays: cfg.max_age_days != null ? String(cfg.max_age_days) : '',
    timeout: cfg.timeout != null ? String(cfg.timeout) : '',
    mode: webMode,
    browserMode,
    browserPreset: cfg.preset || '',
    browserMaxScrolls: rawMaxScrolls,
    selItem: selectorStr(cfg.selectors, 'item'),
    selTitle: selectorStr(cfg.selectors, 'title'),
    selLink: selectorStr(cfg.selectors, 'link'),
    selSummary: selectorStr(cfg.selectors, 'summary'),
    selDate: selectorStr(cfg.selectors, 'date'),
    selector: cfg.selector || '',
    fetchContent: cfg.fetch_content ?? false,
    onDetailError: cfg.on_detail_error || (cfg.type === 'browser' ? 'record_and_skip' : 'fail_batch'),
    emitBaseline: cfg.emit_baseline ?? true,
    maxInputChars: cfg.max_input_chars != null ? String(cfg.max_input_chars) : '20000',
    model: cfg.model || '',
    concurrency: cfg.concurrency != null ? String(cfg.concurrency) : '4',
    pageUrl: cfg.page_url || '',
    task: cfg.task || '',
    chromeProfile: cfg.chrome_profile || '',
    maxItems: cfg.max_items != null ? String(cfg.max_items) : '25',
    host: cfg.host || 'imap.gmail.com',
    port: cfg.port != null ? String(cfg.port) : '',
    ssl: cfg.ssl ?? true,
    folders: (cfg.folders || ['INBOX']).join('\n'),
    includeSubfolders: cfg.include_subfolders ?? false,
    initialDays: cfg.initial_days != null ? String(cfg.initial_days) : '30',
    maxFetch: cfg.max_fetch != null ? String(cfg.max_fetch) : '100',
    search: cfg.search || 'ALL',
    excludeSenders: (cfg.exclude_senders || []).join('\n'),
    pipelines: cfg.pipelines || [],
  }
}

const parseLines = (raw: string) => raw.split(/\r?\n/).map(v => v.trim()).filter(Boolean)

function optionalNumber(raw: string): number | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function optionalInt(raw: string): number | null {
  const parsed = optionalNumber(raw)
  return parsed === null ? null : Math.trunc(parsed)
}

function applyNonImapFields(
  target: SourceCreate | SourceUpdate,
  draft: SourceDraft,
  type: CreatableSourceType,
  includeNullTimeout: boolean,
): void {
  const timeout = optionalNumber(draft.timeout)
  if (timeout !== null || includeNullTimeout) target.timeout = timeout
  if (type === 'rss') {
    target.url = draft.url.trim()
    target.max_age_days = optionalInt(draft.maxAgeDays)
  } else if (type === 'web') {
    target.url = draft.url.trim()
    target.mode = draft.mode
    if (draft.mode === 'list') {
      target.selectors = {
        item: draft.selItem.trim(),
        title: draft.selTitle.trim(),
        link: draft.selLink.trim(),
        ...(draft.selSummary.trim() ? { summary: draft.selSummary.trim() } : {}),
        ...(draft.selDate.trim() ? { date: draft.selDate.trim() } : {}),
      }
      target.fetch_content = draft.fetchContent
      target.on_detail_error = draft.onDetailError
      target.concurrency = optionalInt(draft.concurrency)
    } else if (draft.mode === 'page') {
      target.selector = draft.selector.trim() || null
      target.emit_baseline = draft.emitBaseline
    } else if (draft.mode === 'llm') {
      target.model = draft.model.trim() || null
      target.fetch_content = draft.fetchContent
      target.max_input_chars = optionalInt(draft.maxInputChars)
      target.on_detail_error = draft.onDetailError
      target.concurrency = optionalInt(draft.concurrency)
    }
  } else if (type === 'browser') {
    target.page_url = draft.pageUrl.trim()
    target.chrome_profile = draft.chromeProfile.trim() || null
    target.max_items = optionalInt(draft.maxItems)
    if (draft.browserMode === 'list') {
      target.mode = 'list'
      if (draft.browserPreset) target.preset = draft.browserPreset
      const maxScrolls = optionalInt(draft.browserMaxScrolls)
      if (maxScrolls !== null && !includeNullTimeout) {
        target.scroll = { max_scrolls: maxScrolls }
      }
    } else {
      target.mode = 'script'
      target.task = draft.task.trim() || null
      target.on_detail_error = draft.onDetailError
    }
  }
}

function toCreatePayload(draft: SourceDraft): SourceCreate {
  const type: CreatableSourceType = draft.type === 'imap' ? 'rss' : draft.type
  const base: SourceCreate = {
    id: draft.id.trim(),
    type,
    name: draft.name.trim() || null,
    schedule: draft.schedule.trim(),
    enabled: draft.enabled,
    update_existing: type === 'web' && draft.mode === 'page' ? false : draft.updateExisting,
    pipelines: draft.pipelines,
  }
  applyNonImapFields(base, draft, type, false)
  return base
}

function toUpdatePayload(draft: SourceDraft, existingConfig?: SourceConfig): SourceUpdate {
  const patch: SourceUpdate = {
    name: draft.name.trim() || null,
    schedule: draft.schedule.trim(),
    enabled: draft.enabled,
    pipelines: draft.pipelines,
  }
  if (draft.type === 'browser' && draft.browserMode === 'list') {
    patch.update_existing = draft.updateExisting
    patch.timeout = optionalNumber(draft.timeout)
    patch.page_url = draft.pageUrl.trim()
    patch.chrome_profile = draft.chromeProfile.trim() || null
    patch.max_items = optionalInt(draft.maxItems)
    patch.mode = 'list'
    if (draft.browserPreset) patch.preset = draft.browserPreset
    const maxScrolls = optionalInt(draft.browserMaxScrolls)
    if (maxScrolls !== null) {
      patch.scroll = { ...(existingConfig?.scroll || {}), max_scrolls: maxScrolls }
    }
  } else if (draft.type !== 'imap') {
    patch.update_existing = draft.type === 'web' && draft.mode === 'page' ? false : draft.updateExisting
    applyNonImapFields(patch, draft, draft.type, true)
  } else {
    patch.timeout = optionalNumber(draft.timeout)
    patch.folders = parseLines(draft.folders)
    patch.include_subfolders = draft.includeSubfolders
    patch.initial_days = optionalInt(draft.initialDays)
    patch.max_fetch = optionalInt(draft.maxFetch)
    patch.search = draft.search.trim()
    patch.exclude_senders = parseLines(draft.excludeSenders)
  }
  return patch
}

function SourceForm({
  draft,
  setDraft,
  isCreate,
  pipelines,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  draft: SourceDraft
  setDraft: (next: SourceDraft) => void
  isCreate: boolean
  pipelines: { id: string; name?: string | null }[]
  busy: boolean
  error: Error | null
  onSubmit: () => void
  onCancel: () => void
}) {
  const togglePipeline = (pid: string) => {
    const has = draft.pipelines.includes(pid)
    setDraft({
      ...draft,
      pipelines: has ? draft.pipelines.filter(id => id !== pid) : [...draft.pipelines, pid],
    })
  }
  const timeoutPlaceholder = draft.type === 'browser' ? '默认 60' : '默认 30'

  return (
    <form className="source-form" onSubmit={event => { event.preventDefault(); onSubmit() }}>
      {error && <Notice error>{error.message}</Notice>}

      <div className="form-row">
        <label>
          信息源 ID
          <input
            name="source-id"
            value={draft.id}
            disabled={!isCreate || busy}
            onChange={event => setDraft({ ...draft, id: event.target.value })}
            placeholder="例如：simonwillison-blog"
            required
          />
        </label>
        <label>
          类型
          <select
            name="source-type"
            value={draft.type}
            disabled={!isCreate || busy}
            onChange={event => {
              const nextType = event.target.value as SourceType
              setDraft({
                ...draft,
                type: nextType,
                onDetailError: nextType === 'browser' ? 'record_and_skip' : 'fail_batch',
              })
            }}
          >
            <option value="rss">RSS / Atom 订阅</option>
            <option value="web">Web 网页信息源</option>
            <option value="browser">有界面浏览器源</option>
            {!isCreate && <option value="imap">IMAP 邮箱</option>}
          </select>
        </label>
      </div>

      <div className="form-row">
        <label>
          显示名称（可选）
          <input
            name="source-name"
            value={draft.name}
            disabled={busy}
            onChange={event => setDraft({ ...draft, name: event.target.value })}
            placeholder="留空则显示 ID"
          />
        </label>
        <label>
          抓取频率（schedule）
          <input
            name="source-schedule"
            value={draft.schedule}
            disabled={busy}
            onChange={event => setDraft({ ...draft, schedule: event.target.value })}
            placeholder="30m / 1h / 2h / @daily / cron"
            required
          />
        </label>
      </div>

      <div className="form-row">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={draft.enabled}
            disabled={busy}
            onChange={event => setDraft({ ...draft, enabled: event.target.checked })}
          />
          启用自动调度抓取
        </label>
        {draft.type !== 'imap' && !(draft.type === 'web' && draft.mode === 'page') && (
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={draft.updateExisting}
              disabled={busy}
              onChange={event => setDraft({ ...draft, updateExisting: event.target.checked })}
            />
            允许原位更新（update_existing）
          </label>
        )}
      </div>

      {draft.type === 'rss' && <>
        <label>
          Feed URL
          <input
            name="rss-url"
            type="text"
            value={draft.url}
            disabled={busy}
            onChange={event => setDraft({ ...draft, url: event.target.value })}
            placeholder="https://example.com/feed.xml"
            required
          />
        </label>
        <div className="form-row">
          <label>
            最大文章天数（max_age_days，可选）
            <input
              name="rss-max-age"
              type="number"
              min={0}
              value={draft.maxAgeDays}
              disabled={busy}
              onChange={event => setDraft({ ...draft, maxAgeDays: event.target.value })}
              placeholder="留空不限制"
            />
          </label>
          <label>
            请求超时（秒）
            <input
              name="source-timeout"
              type="number"
              min={1}
              step="any"
              value={draft.timeout}
              disabled={busy}
              placeholder={timeoutPlaceholder}
              onChange={event => setDraft({ ...draft, timeout: event.target.value })}
            />
          </label>
        </div>
      </>}

      {draft.type === 'web' && <>
        <div className="form-row">
          <label>
            抓取模式（mode）
            <select
              name="web-mode"
              value={draft.mode}
              disabled={busy}
              onChange={event => setDraft({ ...draft, mode: event.target.value as WebMode })}
            >
              <option value="list">list（CSS 选择器列表）</option>
              <option value="page">page（单页内容变化）</option>
              <option value="llm">llm（模型智能抽取）</option>
            </select>
          </label>
          <label>
            请求超时（秒）
            <input
              name="web-timeout"
              type="number"
              min={1}
              step="any"
              value={draft.timeout}
              disabled={busy}
              placeholder={timeoutPlaceholder}
              onChange={event => setDraft({ ...draft, timeout: event.target.value })}
            />
          </label>
        </div>
        <label>
          目标网页 URL
          <input
            name="web-url"
            type="text"
            value={draft.url}
            disabled={busy}
            onChange={event => setDraft({ ...draft, url: event.target.value })}
            placeholder="https://example.com/news"
            required
          />
        </label>

        {draft.mode === 'list' && <>
          <div className="form-row">
            <label>条目容器选择器（item）<input value={draft.selItem} disabled={busy} onChange={e => setDraft({ ...draft, selItem: e.target.value })} required /></label>
            <label>标题选择器（title）<input value={draft.selTitle} disabled={busy} onChange={e => setDraft({ ...draft, selTitle: e.target.value })} required /></label>
          </div>
          <div className="form-row">
            <label>链接选择器（link）<input value={draft.selLink} disabled={busy} onChange={e => setDraft({ ...draft, selLink: e.target.value })} required /></label>
            <label>摘要选择器（summary，可选）<input value={draft.selSummary} disabled={busy} onChange={e => setDraft({ ...draft, selSummary: e.target.value })} /></label>
          </div>
          <div className="form-row">
            <label>时间选择器（date，可选）<input value={draft.selDate} disabled={busy} onChange={e => setDraft({ ...draft, selDate: e.target.value })} /></label>
            <label>详情并发数（1–16）<input type="number" min={1} max={16} value={draft.concurrency} disabled={busy} onChange={e => setDraft({ ...draft, concurrency: e.target.value })} /></label>
          </div>
          <div className="form-row">
            <label className="checkbox-label"><input type="checkbox" checked={draft.fetchContent} disabled={busy} onChange={e => setDraft({ ...draft, fetchContent: e.target.checked })} />并发抓取详情页正文</label>
            <label>
              详情失败策略
              <select value={draft.onDetailError} disabled={busy} onChange={e => setDraft({ ...draft, onDetailError: e.target.value as DetailErrorMode })}>
                <option value="fail_batch">fail_batch（整批回滚）</option>
                <option value="record_and_skip">record_and_skip（记账跳过并重试）</option>
              </select>
            </label>
          </div>
        </>}

        {draft.mode === 'page' && <div className="form-row">
          <label>区域选择器（selector，可选）<input value={draft.selector} disabled={busy} onChange={e => setDraft({ ...draft, selector: e.target.value })} placeholder="例如 main" /></label>
          <label className="checkbox-label"><input type="checkbox" checked={draft.emitBaseline} disabled={busy} onChange={e => setDraft({ ...draft, emitBaseline: e.target.checked })} />首次抓取生成基线条目</label>
        </div>}

        {draft.mode === 'llm' && <>
          <div className="form-row">
            <label>覆盖模型名（可选）<input value={draft.model} disabled={busy} onChange={e => setDraft({ ...draft, model: e.target.value })} placeholder="默认使用 extract 模型" /></label>
            <label>最大输入字符（1–200000）<input type="number" min={1} max={200000} value={draft.maxInputChars} disabled={busy} onChange={e => setDraft({ ...draft, maxInputChars: e.target.value })} /></label>
          </div>
          <div className="form-row">
            <label className="checkbox-label"><input type="checkbox" checked={draft.fetchContent} disabled={busy} onChange={e => setDraft({ ...draft, fetchContent: e.target.checked })} />并发抓取详情页正文</label>
            <label>详情并发数（1–16）<input type="number" min={1} max={16} value={draft.concurrency} disabled={busy} onChange={e => setDraft({ ...draft, concurrency: e.target.value })} /></label>
          </div>
          {draft.fetchContent && (
            <label>
              详情失败策略
              <select value={draft.onDetailError} disabled={busy} onChange={e => setDraft({ ...draft, onDetailError: e.target.value as DetailErrorMode })}>
                <option value="fail_batch">fail_batch（整批回滚）</option>
                <option value="record_and_skip">record_and_skip（记账跳过并重试）</option>
              </select>
            </label>
          )}
        </>}
      </>}

      {draft.type === 'browser' && <>
        <div className="form-row">
          <label>
            浏览器抓取模式
            <select
              name="browser-mode"
              value={draft.browserMode}
              disabled={busy}
              onChange={e => setDraft({ ...draft, browserMode: e.target.value as BrowserMode })}
            >
              <option value="script">script（重放固化 Playwright 脚本）</option>
              <option value="list">list（无限滚动选择器与互动量预筛）</option>
            </select>
          </label>
          <label>单次超时（秒）<input type="number" min={1} step="any" value={draft.timeout} disabled={busy} placeholder={timeoutPlaceholder} onChange={e => setDraft({ ...draft, timeout: e.target.value })} /></label>
        </div>
        <label>
          入口页 URL（page_url）
          <input
            name="browser-url"
            type="text"
            value={draft.pageUrl}
            disabled={busy}
            onChange={event => setDraft({ ...draft, pageUrl: event.target.value })}
            placeholder="https://www.reddit.com/r/LocalLLaMA/new/"
            required
          />
        </label>
        <div className="form-row">
          <label>Chrome Profile（可选）<input value={draft.chromeProfile} disabled={busy} onChange={e => setDraft({ ...draft, chromeProfile: e.target.value })} placeholder="留空使用默认 Profile" /></label>
          <label>单轮最多条数（1–500）<input type="number" min={1} max={500} value={draft.maxItems} disabled={busy} onChange={e => setDraft({ ...draft, maxItems: e.target.value })} /></label>
        </div>
        {draft.browserMode === 'script' ? <>
          <label>
            采集任务说明（task，冷启动时供 Agent 探索）
            <textarea
              rows={3}
              value={draft.task}
              disabled={busy}
              onChange={event => setDraft({ ...draft, task: event.target.value })}
            />
          </label>
          <label>冷启动失败策略
            <select value={draft.onDetailError} disabled={busy} onChange={e => setDraft({ ...draft, onDetailError: e.target.value as DetailErrorMode })}>
              <option value="record_and_skip">record_and_skip</option>
              <option value="fail_batch">fail_batch</option>
            </select>
          </label>
        </> : <>
          <div className="form-row">
            <label>内置站点预设（preset）
              <select
                value={draft.browserPreset}
                disabled={busy}
                onChange={e => setDraft({ ...draft, browserPreset: e.target.value as BrowserPreset })}
                required={isCreate}
              >
                {!isCreate && <option value="">自定义 YAML 选择器（保持不变）</option>}
                <option value="reddit">reddit（Reddit 自定义流 / Subreddit）</option>
                <option value="x-following">x-following（X 正在关注时间流）</option>
              </select>
            </label>
            <label>最大下翻次数（max_scrolls）
              <input
                type="number"
                min={0}
                max={200}
                value={draft.browserMaxScrolls}
                disabled={busy}
                placeholder="默认 20"
                onChange={e => setDraft({ ...draft, browserMaxScrolls: e.target.value })}
              />
            </label>
          </div>
          <p className="small muted">细粒度选择器（selectors）、互动阈值（prefilter）与等待时间将原样保留在 tentacles.yaml 中。</p>
        </>}
      </>}

      {draft.type === 'imap' && <>
        <p className="small muted">邮箱连接端点（主机、端口、SSL）与凭据由 tentacles.yaml / .env 管理；此处仅调整文件夹、查询条件与抓取限额。</p>
        <div className="form-row">
          <label>IMAP 主机（只读）<input value={draft.host} disabled readOnly /></label>
          <label>端口 / 传输（只读）<input value={`${draft.port || (draft.ssl ? '993' : '143')} (${draft.ssl ? 'SSL' : '非 SSL'})`} disabled readOnly /></label>
        </div>
        <div className="form-row">
          <label className="checkbox-label"><input type="checkbox" checked={draft.includeSubfolders} disabled={busy} onChange={e => setDraft({ ...draft, includeSubfolders: e.target.checked })} />递归读取全部子文件夹</label>
        </div>
        <label>
          监听文件夹（每行一个，多级用 / 分隔）
          <textarea rows={3} value={draft.folders} disabled={busy} onChange={e => setDraft({ ...draft, folders: e.target.value })} required />
        </label>
        <div className="form-row">
          <label>首次回溯天数<input type="number" min={1} value={draft.initialDays} disabled={busy} onChange={e => setDraft({ ...draft, initialDays: e.target.value })} /></label>
          <label>单轮最多拉取封数<input type="number" min={1} max={10000} value={draft.maxFetch} disabled={busy} onChange={e => setDraft({ ...draft, maxFetch: e.target.value })} /></label>
        </div>
        <div className="form-row">
          <label>IMAP SEARCH 条件<input value={draft.search} disabled={busy} onChange={e => setDraft({ ...draft, search: e.target.value })} /></label>
          <label>超时（秒）<input type="number" min={1} step="any" value={draft.timeout} disabled={busy} placeholder={timeoutPlaceholder} onChange={e => setDraft({ ...draft, timeout: e.target.value })} /></label>
        </div>
        <label>
          排除发件人（每行一个完整邮箱）
          <textarea rows={2} value={draft.excludeSenders} disabled={busy} onChange={e => setDraft({ ...draft, excludeSenders: e.target.value })} />
        </label>
      </>}

      {pipelines.length > 0 && (
        <div>
          <span className="small muted">关联处理流程（Pipelines）</span>
          <div className="pipeline-checks">
            {pipelines.map(p => (
              <label key={p.id} className="checkbox-label">
                <input
                  type="checkbox"
                  checked={draft.pipelines.includes(p.id)}
                  disabled={busy}
                  onChange={() => togglePipeline(p.id)}
                />
                {p.name || p.id}
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="drawer-actions">
        <button type="button" disabled={busy} onClick={onCancel}>取消</button>
        <button type="submit" className="primary" disabled={busy}>{busy ? '保存中…' : isCreate ? '创建并启用' : '保存修改'}</button>
      </div>
    </form>
  )
}

function EditSourceDrawer({
  sourceId,
  pipelines,
  invalidateAll,
  close,
  onSaved,
}: {
  sourceId: string
  pipelines: { id: string; name?: string | null }[]
  invalidateAll: () => Promise<unknown>
  close: () => void
  onSaved: (message: string) => void
}) {
  const client = useQueryClient()
  const detail = useQuery({
    queryKey: ['source-config', sourceId],
    queryFn: () => api('/api/sources/{source_id}', 'get', { path: { source_id: sourceId } }),
  })
  // Snapshot once fresh data arrives for sourceId so stale caches cannot hydrate old toggles,
  // and later window-focus refetches never wipe unsaved edits.
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<SourceDraft | null>(null)
  useEffect(() => {
    if (loadedId !== sourceId) {
      setDraft(null)
      setLoadedId(null)
    }
    if (detail.data && !detail.isFetching && !detail.isError && loadedId !== sourceId) {
      setDraft(draftFromConfig(detail.data))
      setLoadedId(sourceId)
    }
  }, [detail.data, detail.isError, detail.isFetching, loadedId, sourceId])

  const save = useMutation({
    mutationFn: (next: SourceDraft) => api('/api/sources/{source_id}', 'patch', {
      path: { source_id: sourceId },
      body: toUpdatePayload(next, detail.data),
    }),
    onSuccess: async data => {
      client.setQueryData(['source-config', data.id], data)
      await invalidateAll()
      onSaved(`已保存信息源「${sourceId}」的配置并刷新调度。`)
      close()
    },
  })

  return (
    <Drawer title={`编辑信息源 · ${sourceId}`} eyebrow="EDIT SOURCE" close={close}>
      {(detail.isPending || (!draft && detail.isFetching)) && <Notice>正在读取配置…</Notice>}
      {detail.error && <Notice error>{detail.error.message}</Notice>}
      {draft && (
        <SourceForm
          draft={draft}
          setDraft={setDraft}
          isCreate={false}
          pipelines={pipelines}
          busy={save.isPending}
          error={save.error}
          onSubmit={() => save.mutate(draft)}
          onCancel={close}
        />
      )}
    </Drawer>
  )
}

export default function Sources() {
  const { formatTime, error: displayError } = useDisplayTime()
  const client = useQueryClient()
  const [filter, setFilter] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [createDraft, setCreateDraft] = useState<SourceDraft>(() => emptyDraft())
  const [editingId, setEditingId] = useState<string | null>(null)
  const [togglingIds, setTogglingIds] = useState<Set<string>>(() => new Set())
  const [notice, setNotice] = useState('')

  const query = useQuery({ queryKey: ['sources'], queryFn: () => api('/api/sources', 'get'), refetchInterval: 30000 })
  const pipelinesQuery = useQuery({ queryKey: ['pipelines'], queryFn: () => api('/api/pipelines', 'get') })

  const invalidateAll = () => Promise.all(
    ['sources', 'source-config', 'items', 'item', 'pipelines', 'runs', 'scheduler'].map(key =>
      client.invalidateQueries({ queryKey: [key] }),
    ),
  )

  const fetch = useMutation({
    mutationFn: (id: string) => api('/api/sources/{source_id}/fetch', 'post', { path: { source_id: id } }),
    onSettled: invalidateAll,
  })

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api('/api/sources/{source_id}', 'patch', { path: { source_id: id }, body: { enabled } }),
    onMutate: ({ id }) => {
      setTogglingIds(prev => new Set(prev).add(id))
    },
    onSuccess: async data => {
      client.setQueryData(['source-config', data.id], data)
      setNotice(`已${data.enabled ? '启用' : '停用'}信息源「${data.name || data.id}」。`)
      await invalidateAll()
    },
    onSettled: (_data, _error, { id }) => {
      setTogglingIds(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    },
  })

  const create = useMutation({
    mutationFn: (draft: SourceDraft) => api('/api/sources', 'post', { body: toCreatePayload(draft) }),
    onSuccess: async data => {
      client.setQueryData(['source-config', data.id], data)
      setShowCreate(false)
      setCreateDraft(emptyDraft())
      setNotice(`已新增信息源「${data.name || data.id}」并加入调度。`)
      await invalidateAll()
    },
  })

  const needle = filter.trim().toLowerCase()
  const visibleSources = (query.data || []).filter(source =>
    !needle ||
    source.id.toLowerCase().includes(needle) ||
    (source.name || '').toLowerCase().includes(needle) ||
    source.type.toLowerCase().includes(needle),
  )
  const pipelineList = (pipelinesQuery.data || []).map(p => ({ id: p.id, name: p.name }))

  return <>
    <header className="page-heading">
      <div>
        <p className="eyebrow">CONNECTED TO YOUR WORLD</p>
        <h1>信息源<span className="accent">.</span></h1>
        <p className="muted">每一条信息，都有迹可循。</p>
      </div>
      <div className="source-actions">
        <div className="total"><strong>{query.data?.length ?? '—'}</strong><span>个已配置来源</span></div>
        <button className="primary" onClick={() => { create.reset(); setShowCreate(true) }}>+ 新增信息源</button>
      </div>
    </header>

    <div className="sources-toolbar">
      <p className="section-note">支持在线新增、编辑与一键启停，变更会写回 YAML 并立即生效；手动抓取也可测试已停用的源。</p>
      <input
        type="search"
        value={filter}
        onChange={event => setFilter(event.target.value)}
        placeholder="按 ID、名称或类型筛选…"
        aria-label="筛选信息源"
      />
    </div>

    {notice && <Notice>{notice}</Notice>}
    {displayError && <Notice error>{displayError.message}</Notice>}
    {query.error && <Notice error>{query.error.message} <button onClick={() => void query.refetch()}>重试</button></Notice>}
    {toggle.error && <Notice error>{toggle.error.message}</Notice>}
    {query.isPending && <Notice>正在读取信息源…</Notice>}
    {fetch.error && <Notice error>{fetch.error.message}</Notice>}
    {fetch.data && !fetch.isPending && <Notice>抓取完成（{fetch.data.source_id}）：新增 {fetch.data.new} 条，更新 {fetch.data.updated ?? 0} 条，重复 {fetch.data.dup} 条。</Notice>}

    {query.data && query.data.length === 0 && (
      <div className="empty"><h2>尚未配置来源</h2><p>点击右上角「+ 新增信息源」添加 RSS、网页或浏览器源。</p></div>
    )}

    {!!query.data?.length && visibleSources.length === 0 && (
      <div className="empty"><h2>无匹配信息源</h2><p>没有匹配「{filter.trim()}」的信息源。</p></div>
    )}

    {!!visibleSources.length && (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>来源 / 类型</th>
              <th>启停</th>
              <th>最近状态</th>
              <th>条目</th>
              <th>下次运行</th>
              <th><span className="sr-only">操作</span></th>
            </tr>
          </thead>
          <tbody>
            {visibleSources.map(source => {
              const togglingThis = togglingIds.has(source.id)
              const fetchingThis = fetch.isPending && fetch.variables === source.id
              return (
                <tr key={source.id}>
                  <td>
                    <strong>{source.name || source.id}</strong>
                    <div className="muted small">{source.id} · {source.type.toUpperCase()} · {source.schedule}</div>
                  </td>
                  <td>
                    <div className="switch-wrap">
                      <button
                        type="button"
                        role="switch"
                        className="switch"
                        aria-checked={source.enabled}
                        aria-label={`切换 ${source.name || source.id} 启用状态`}
                        disabled={togglingThis}
                        onClick={() => toggle.mutate({ id: source.id, enabled: !source.enabled })}
                      />
                      <span className="small muted">{source.enabled ? '已启用' : '已停用'}</span>
                    </div>
                  </td>
                  <td>
                    <span className={`status ${source.last_status === 'error' ? 'bad' : ''}`}>
                      {source.last_status === 'ok' ? '正常' : source.last_status === 'error' ? '异常' : '尚未抓取'}
                    </span>
                    <div className="muted small">{formatTime(source.last_fetch_at)}</div>
                    {source.last_error && <div className="small error-text">{source.last_error}</div>}
                  </td>
                  <td className="tabular">{source.total_items}</td>
                  <td className="tabular">{formatTime(source.next_run_at)}</td>
                  <td>
                    <div className="source-actions">
                      <button type="button" onClick={() => setEditingId(source.id)}>编辑</button>
                      <button
                        type="button"
                        disabled={fetch.isPending}
                        onClick={() => fetch.mutate(source.id)}
                      >
                        {fetchingThis ? '抓取中…' : '立即抓取'}
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )}

    {showCreate && (
      <Drawer title="新增信息源" eyebrow="ADD SOURCE" close={() => setShowCreate(false)}>
        <SourceForm
          draft={createDraft}
          setDraft={setCreateDraft}
          isCreate={true}
          pipelines={pipelineList}
          busy={create.isPending}
          error={create.error}
          onSubmit={() => create.mutate(createDraft)}
          onCancel={() => setShowCreate(false)}
        />
      </Drawer>
    )}

    {editingId && (
      <EditSourceDrawer
        sourceId={editingId}
        pipelines={pipelineList}
        invalidateAll={invalidateAll}
        close={() => setEditingId(null)}
        onSaved={setNotice}
      />
    )}
  </>
}
