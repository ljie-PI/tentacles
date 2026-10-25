export function formatCost(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '未知' : `$${value.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 8 })}`
}

export function formatDuration(started: string, finished: string | null | undefined, status: string): string {
  if (!finished) return status === 'running' ? '运行中' : '—'
  const seconds = (new Date(finished).valueOf() - new Date(started).valueOf()) / 1000
  return Number.isFinite(seconds) ? `${Math.max(0, seconds).toFixed(1)}s` : '—'
}

export function formatStats(stats: Record<string, unknown>): string {
  const fields = [['new', '新增'], ['dup', '重复'], ['evaluated', '已评估'], ['passed', '通过'], ['rejected', '未通过'], ['consumed', '已消费']] as const
  const values = fields.flatMap(([key, label]) => typeof stats[key] === 'number' ? [`${label} ${stats[key]}`] : [])
  if (stats.no_new === true) values.push('无新内容')
  return values.slice(0, 4).join(' · ') || '无摘要字段'
}
