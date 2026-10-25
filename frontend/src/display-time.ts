import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api } from './api'
import type { components } from './api-types'

type DisplaySettings = components['schemas']['DisplaySettings']
export type TimeFormatter = (value: string | null | undefined) => string

export function useDisplayTime() {
  const query = useQuery({ queryKey: ['display-settings'], queryFn: () => api<DisplaySettings>('/settings') })
  const timezone = query.data?.timezone
  const formatter = useMemo(() => {
    if (!timezone) return null
    try {
      return new Intl.DateTimeFormat('zh-CN', { timeZone: timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    } catch { return null }
  }, [timezone])
  const formatTime: TimeFormatter = value => {
    if (!value) return '—'
    const date = new Date(value)
    if (Number.isNaN(date.valueOf())) return '—'
    if (formatter) return formatter.format(date)
    return timezone ? `${date.toISOString()} (UTC)` : '—'
  }
  const error = query.error || (timezone && !formatter ? new Error('浏览器不支持配置的时区，时间暂以 UTC 显示。') : null)
  return { formatTime, error, timezone }
}
