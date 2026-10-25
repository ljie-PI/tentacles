import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { diffLines } from 'diff'
import { api, ApiError, type Persona, type PersonaProposal } from './api'
import { useDisplayTime } from './display-time'
import { Notice } from './shared'

type Draft = { profile: string; include: string; exclude: string; note: string; updateTags: boolean }
const lines = (value: string) => value.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
const draftOf = (persona?: Persona): Draft => ({ profile: persona?.profile || '', include: (persona?.include || []).join('\n'), exclude: (persona?.exclude || []).join('\n'), note: '', updateTags: true })
const isConflict = (error: Error | null) => error instanceof ApiError && error.status === 409

function ProfileInputs({ value, change, disabled }: { value: Draft; change: (value: Draft) => void; disabled: boolean }) {
  return <>
    <label>兴趣描述 profile<textarea name="profile" value={value.profile} disabled={disabled} onChange={event => change({ ...value, profile: event.target.value })} rows={9} /></label>
    <div className="keyword-grid"><label>包含关键词（每行一个）<textarea name="include" value={value.include} disabled={disabled} onChange={event => change({ ...value, include: event.target.value })} rows={4} /></label><label>排除关键词（每行一个）<textarea name="exclude" value={value.exclude} disabled={disabled} onChange={event => change({ ...value, exclude: event.target.value })} rows={4} /></label></div>
    <label>本次变更备注<input name="note" value={value.note} disabled={disabled} onChange={event => change({ ...value, note: event.target.value })} /></label>
    <label className="checkbox-label"><input type="checkbox" checked={value.updateTags} disabled={disabled} onChange={event => change({ ...value, updateTags: event.target.checked })} />修改 profile 时自动抽取标签（可能产生模型费用）</label>
  </>
}

function Tags({ persona }: { persona: Pick<Persona, 'tags' | 'tag_status' | 'tag_error'> }) {
  return <div className="readonly-tags"><h3>兴趣标签 <span className="small muted">只读</span></h3><div className="tags">{persona.tags?.map(tag => <span className="tag" key={tag.tag} title={tag.description}>{tag.tag}</span>)}{!persona.tags?.length && <span className="small muted">尚无标签</span>}</div>{persona.tag_status === 'error' && <Notice error>画像已保存，但标签抽取失败，保留旧标签。{persona.tag_error}</Notice>}</div>
}

function ProposalPreview({ base, proposal }: { base: Persona; proposal: PersonaProposal }) {
  const changes = useMemo(() => diffLines(base.profile, proposal.profile, { timeout: 300, maxEditLength: 10000, oneChangePerToken: true }), [base.profile, proposal.profile])
  const oldTags = new Set((base.tags || []).map(tag => tag.tag))
  const newTags = new Set(proposal.tags.map(tag => tag.tag))
  const added = proposal.tags.filter(tag => !oldTags.has(tag.tag))
  const removed = (base.tags || []).filter(tag => !newTags.has(tag.tag))
  return <section className="proposal-preview"><h3>演化提案预览</h3><p className="small muted">基于版本 {proposal.base_version} · 使用了 {proposal.feedback_count} 条反馈</p><p className="change-summary">{proposal.summary}</p>
    <h4>Profile 逐行差异</h4><p className="small muted">＋ 新增 / − 删除</p>{changes ? <pre className="profile-diff">{changes.map((part, index) => <span key={index} className={part.added ? 'diff-added' : part.removed ? 'diff-removed' : ''}>{part.added ? '+ ' : part.removed ? '- ' : '  '}{part.value}</span>)}</pre> : <><Notice>差异过大，改为显示完整的旧画像和提案，避免阻塞页面。</Notice><div className="keyword-grid"><pre className="profile-text">{base.profile}</pre><pre className="profile-text">{proposal.profile}</pre></div></>}
    <h4>标签增减</h4><p className="small">新增：{added.map(tag => tag.tag).join('、') || '无'}<br />移除：{removed.map(tag => tag.tag).join('、') || '无'}</p><div className="tags">{proposal.tags.map(tag => <span className="tag" key={tag.tag} title={tag.description}>{tag.tag}</span>)}</div>
    <p className="small">提案包含关键词：{proposal.include.join('、') || '无'}<br />提案排除关键词：{proposal.exclude.join('、') || '无'}</p>
  </section>
}

function CreatePersona({ created, activityChanged }: { created: (value: Persona) => void; activityChanged: (value: boolean) => void }) {
  const client = useQueryClient()
  const [name, setName] = useState('')
  const [draft, setDraft] = useState<Draft>(() => draftOf())
  const create = useMutation({ mutationFn: () => api('/api/personas', 'patch', { body: { name: name.trim(), expected_version: 0, profile: draft.profile, include_add: lines(draft.include), exclude_add: lines(draft.exclude), skip_tags: !draft.updateTags, note: draft.note } }), onSuccess: value => { client.setQueryData(['persona', value.name, 'current'], value); client.setQueryData<Persona[]>(['personas'], old => [...(old || []).filter(row => row.name !== value.name), value]); void client.invalidateQueries({ queryKey: ['personas'] }) } })
  useEffect(() => { activityChanged(create.isPending); return () => activityChanged(false) }, [create.isPending, activityChanged])
  return <section className="persona-panel"><h2>新建 Persona</h2><form className="persona-form" onSubmit={event => { event.preventDefault(); create.mutate(undefined, { onSuccess: created }) }}><label>名称<input name="persona-name" value={name} disabled={create.isPending} onChange={event => setName(event.target.value)} required /></label><ProfileInputs value={draft} change={setDraft} disabled={create.isPending} /><button className="primary" disabled={create.isPending || !name.trim()}>{create.isPending ? '保存中…' : '创建画像'}</button></form>{create.error && <Notice error>{isConflict(create.error) ? '该名称已有画像，请刷新列表后查看。' : create.error.message}</Notice>}</section>
}

function PersonaEditor({ persona, workChanged }: { persona: Persona; workChanged: (value: boolean) => void }) {
  const client = useQueryClient()
  // The edit snapshot is deliberate. A query refresh must never silently reset
  // an unsaved draft or replace its compare-and-swap version.
  const [base, setBase] = useState(persona)
  const [draft, setDraft] = useState(() => draftOf(persona))
  const [instruction, setInstruction] = useState('')
  const [useFeedback, setUseFeedback] = useState(true)
  const [preview, setPreview] = useState<{ base: Persona; proposal: PersonaProposal } | null>(null)
  const [viewVersion, setViewVersion] = useState<number | null>(null)
  const [rollbackVersion, setRollbackVersion] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const { formatTime, error: displayError } = useDisplayTime()
  const history = useQuery({ queryKey: ['persona', persona.name, 'history'], queryFn: () => api('/api/personas/history', 'get', { query: { name: persona.name } }) })
  const dirty = draft.profile !== base.profile || lines(draft.include).join('\n') !== (base.include || []).join('\n') || lines(draft.exclude).join('\n') !== (base.exclude || []).join('\n') || !!draft.note.trim()
  const refresh = async () => { await Promise.all(['personas', 'persona', 'pipelines', 'items', 'item', 'runs'].map(key => client.invalidateQueries({ queryKey: [key] }))) }
  const accept = (value: Persona, notice: string, discardEvolution = false) => { setBase(value); setDraft(draftOf(value)); setPreview(null); setRollbackVersion(null); if (discardEvolution) { setInstruction(''); setUseFeedback(true) }; setMessage(notice); void refresh() }
  const save = useMutation({ mutationFn: () => api('/api/personas', 'patch', { body: { name: base.name, expected_version: base.version, profile: draft.profile, include_add: lines(draft.include), include_remove: base.include || [], exclude_add: lines(draft.exclude), exclude_remove: base.exclude || [], skip_tags: !draft.updateTags, note: draft.note } }), onSuccess: value => accept(value, `已保存为版本 ${value.version}`) })
  const evolve = useMutation({ mutationFn: (snapshot: Persona) => api('/api/personas/evolve', 'post', { body: { name: snapshot.name, expected_version: snapshot.version, instruction, use_feedback: useFeedback } }), onMutate: () => setPreview(null), onSuccess: (proposal, snapshot) => { setPreview({ base: snapshot, proposal }); setMessage('提案已生成，尚未修改画像。') } })
  const apply = useMutation({ mutationFn: (proposal: PersonaProposal) => api('/api/personas/apply', 'post', { body: proposal }), onSuccess: value => accept(value, `提案已应用，当前版本 ${value.version}`, true) })
  const rollback = useMutation({ mutationFn: (version: number) => api('/api/personas/rollback', 'post', { body: { name: base.name, expected_version: base.version, version } }), onSuccess: value => accept(value, `已回滚，创建了新版本 ${value.version}`, true) })
  const busy = save.isPending || evolve.isPending || apply.isPending || rollback.isPending
  const errors = [save.error, evolve.error, apply.error, rollback.error].filter((value): value is Error => !!value)
  const conflict = errors.some(isConflict)
  const historical = history.data?.find(value => value.version === viewVersion)
  async function discardAndRefresh() {
    const value = await api('/api/personas/current', 'get', { query: { name: base.name } })
    accept(value, '已放弃草稿和演化输入，载入服务器最新版本。', true)
    save.reset(); evolve.reset(); apply.reset(); rollback.reset()
  }
  const reload = useMutation({ mutationFn: discardAndRefresh })
  const evolutionDirty = instruction !== '' || !useFeedback
  const hasWork = dirty || evolutionDirty || busy || reload.isPending || !!preview
  useEffect(() => { workChanged(hasWork); return () => workChanged(false) }, [hasWork, workChanged])
  return <>
    <section className="persona-panel"><div className="card-heading"><h2>{base.name} <span className="tag">版本 {base.version}</span></h2><span className="small muted">{dirty ? '有未保存的修改' : '已保存快照'}</span></div>
      {message && <Notice>{message}</Notice>}{displayError && <Notice error>{displayError.message}</Notice>}
      {(conflict || persona.version > base.version) && <Notice error>服务器画像已有新版本，请刷新后重新检查。当前草稿保留；生成新提案或保存前需要处理冲突。<button disabled={busy || reload.isPending} onClick={() => reload.mutate()}>放弃草稿并刷新</button></Notice>}
      {errors.filter(error => !isConflict(error)).map((error, index) => <Notice error key={index}>{error.message}</Notice>)}{reload.error && <Notice error>{reload.error.message}</Notice>}
      <form className="persona-form" onSubmit={event => { event.preventDefault(); save.mutate() }}><ProfileInputs value={draft} change={setDraft} disabled={busy || reload.isPending} /><button className="primary" disabled={busy || reload.isPending || !dirty}>{save.isPending ? '保存中…' : '保存新版本'}</button>{dirty && <button type="button" disabled={busy || reload.isPending} onClick={() => { setDraft(draftOf(base)); setMessage('已放弃未保存的手动修改。') }}>放弃手动修改</button>}</form><Tags persona={base} />
    </section>
    <section className="persona-panel"><h2>演化画像</h2><p className="section-note">根据指令和可选反馈生成提案，预览后才应用。生成提案会调用模型，可能产生费用；未保存的手动修改不会发送。</p>
      <label>演化指令<textarea name="instruction" rows={4} value={instruction} disabled={busy || reload.isPending} onChange={event => { setInstruction(event.target.value); setPreview(null) }} placeholder="例如：更看重有实证的研究，减少营销内容" /></label><label className="checkbox-label"><input type="checkbox" checked={useFeedback} disabled={busy || reload.isPending} onChange={event => { setUseFeedback(event.target.checked); setPreview(null) }} />参考近期反馈（服务端窗口和数量限制生效）</label>
      {dirty && <p className="small muted">请先保存或放弃手动修改，再生成演化提案。</p>}<button disabled={busy || reload.isPending || dirty || (!instruction.trim() && !useFeedback)} onClick={() => evolve.mutate(base)}>{evolve.isPending ? '生成中…' : '生成演化提案'}</button>{evolutionDirty && <button disabled={busy || reload.isPending} onClick={() => { setInstruction(''); setUseFeedback(true); setPreview(null); setMessage('已清空演化输入。') }}>清空演化输入</button>}
      {preview && <><ProposalPreview base={preview.base} proposal={preview.proposal} /><div className="pipeline-actions"><button className="primary" disabled={busy || reload.isPending || dirty} onClick={() => apply.mutate(preview.proposal)}>{apply.isPending ? '应用中…' : '应用提案'}</button><button disabled={busy} onClick={() => setPreview(null)}>丢弃提案</button></div></>}
    </section>
    <section className="persona-panel"><h2>版本历史</h2>{history.error && <Notice error>{history.error.message}</Notice>}{history.isPending && <Notice>正在读取历史…</Notice>}
      {history.data && <div className="table-wrap"><table><thead><tr><th>版本 / 来源</th><th>备注 / 时间</th><th>操作</th></tr></thead><tbody>{history.data.map(version => <tr key={version.version}><td><strong>v{version.version}</strong><span className="small muted">{version.source}</span></td><td>{version.note || '—'}<div className="small muted">{formatTime(version.created_at)}</div></td><td><div className="pipeline-actions"><button disabled={busy} onClick={() => setViewVersion(version.version)}>查看</button><button disabled={busy || reload.isPending} onClick={() => setRollbackVersion(version.version)}>回滚到此版本</button></div></td></tr>)}</tbody></table></div>}
      {historical && <div className="history-preview"><h3>查看版本 {historical.version} <span className="small muted">只读</span></h3><pre className="profile-text">{historical.profile}</pre><p className="small">包含：{historical.include?.join('、') || '无'}<br />排除：{historical.exclude?.join('、') || '无'}</p><Tags persona={historical} /></div>}
      {rollbackVersion != null && <Notice>确认回滚到版本 {rollbackVersion}？会创建新版本，替换当前内容、未保存草稿和演化输入；历史不会删除。<button className="primary" disabled={busy || reload.isPending} onClick={() => rollback.mutate(rollbackVersion)}>确认回滚</button><button disabled={busy} onClick={() => setRollbackVersion(null)}>取消</button></Notice>}
    </section>
  </>
}

export default function Personas() {
  const [params, setParams] = useSearchParams()
  const [showCreate, setShowCreate] = useState(false)
  const [hasWork, setHasWork] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [switchTo, setSwitchTo] = useState<string | null>(null)
  const list = useQuery({ queryKey: ['personas'], queryFn: () => api('/api/personas', 'get') })
  const name = params.get('name') || (list.data?.some(value => value.name === 'default') ? 'default' : list.data?.[0]?.name || '')
  const creating = showCreate || list.data?.length === 0
  const current = useQuery({ queryKey: ['persona', name, 'current'], queryFn: () => api('/api/personas/current', 'get', { query: { name } }), enabled: !!name && !creating })
  return <>
    <header className="page-heading"><div><p className="eyebrow">A PROFILE THAT GROWS WITH YOU</p><h1>Persona<span className="accent">.</span></h1><p className="muted">把兴趣写清楚，把改变留有余地。</p></div>{!!list.data?.length && <button disabled={createBusy || (hasWork && !showCreate)} onClick={() => setShowCreate(!showCreate)}>{showCreate ? '放弃新建' : '新建画像'}</button>}</header>
    {list.error && <Notice error>{list.error.message}</Notice>}{list.isPending && <Notice>正在读取画像…</Notice>}
    {!!list.data?.length && <label className="single-filter">选择画像<select value={name} disabled={creating} onChange={event => { if (hasWork) setSwitchTo(event.target.value); else setParams({ name: event.target.value }) }}>{list.data.map(value => <option value={value.name} key={value.name}>{value.name} · v{value.version}</option>)}{name && !list.data.some(value => value.name === name) && <option value={name}>{name}</option>}</select></label>}
    {creating && <CreatePersona activityChanged={setCreateBusy} created={value => { setShowCreate(false); setParams({ name: value.name }) }} />}
    {switchTo != null && <Notice>切换到 {switchTo} 会关闭当前草稿／提案；已开始的操作仍可能完成。<button onClick={() => { setHasWork(false); setParams({ name: switchTo }); setSwitchTo(null) }}>确认切换并放弃</button><button onClick={() => setSwitchTo(null)}>继续编辑</button></Notice>}
    {current.error && <Notice error>{current.error.message}</Notice>}{name && current.isPending && <Notice>正在读取当前版本…</Notice>}
    {!creating && current.data && <PersonaEditor key={current.data.name} persona={current.data} workChanged={setHasWork} />}
  </>
}
