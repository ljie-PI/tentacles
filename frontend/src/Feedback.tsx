import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

// Cache only the display state: optimistic votes do not fabricate server IDs
// or timestamps. Mutation context owns the old persona key across selection changes.
type FeedbackState = { rating: -1 | 1 | null }
type Vote = { itemId: string; persona: string; rating: -1 | 1 }
const feedbackKey = (id: string, persona: string) => ['feedback', id, persona] as const

export default function FeedbackButtons({ itemId, persona, disabled = false }: { itemId: string; persona: string; disabled?: boolean }) {
  const client = useQueryClient()
  const key = feedbackKey(itemId, persona)
  const query = useQuery({ queryKey: key, enabled: !!persona && !disabled,
    queryFn: async () => { const value = await api('/api/items/{item_id}/feedback', 'get', { path: { item_id: itemId }, query: { persona } }); return { rating: value?.rating ?? null } } })
  const active = useIsMutating({ mutationKey: key }) > 0
  const vote = useMutation({ mutationKey: key, retry: false,
    mutationFn: (value: Vote) => api('/api/items/{item_id}/feedback', 'post', { path: { item_id: value.itemId }, body: { persona: value.persona, rating: value.rating, note: '' } }),
    onMutate: async value => {
      const target = feedbackKey(value.itemId, value.persona)
      await client.cancelQueries({ queryKey: target, exact: true })
      const previous = client.getQueryData<FeedbackState>(target) || { rating: null }
      client.setQueryData<FeedbackState>(target, { rating: value.rating })
      return { target, previous }
    },
    onError: (_error, _value, context) => { if (context) client.setQueryData(context.target, context.previous) },
    onSuccess: (result, value) => { client.setQueryData<FeedbackState>(feedbackKey(value.itemId, value.persona), { rating: result.rating }) },
    onSettled: (_result, _error, value) => client.invalidateQueries({ queryKey: feedbackKey(value.itemId, value.persona), exact: true }),
  })
  const rating = query.data?.rating
  const unavailable = disabled || !persona || query.isPending || active
  return <div className="feedback-panel">
    <div className="feedback-buttons" aria-label={`反馈画像：${persona || '未选择'}`}>
      <button className="feedback-button" aria-label="点赞" aria-pressed={rating === 1} disabled={unavailable} onClick={() => { if (rating !== 1) vote.mutate({ itemId, persona, rating: 1 }) }}>＋ 喜欢</button>
      <button className="feedback-button dislike" aria-label="点踩" aria-pressed={rating === -1} disabled={unavailable} onClick={() => { if (rating !== -1) vote.mutate({ itemId, persona, rating: -1 }) }}>− 不感兴趣</button>
      {active && <span className="small muted" role="status">保存中…</span>}
    </div>
    {vote.error && <p className="small error-text" role="alert">反馈请求失败，已撤销乐观显示并重新读取状态：{vote.error.message}</p>}
    {query.error && <p className="small error-text" role="alert">无法读取反馈：{query.error.message}<button onClick={() => void query.refetch()}>重试</button></p>}
  </div>
}
