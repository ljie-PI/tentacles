import type { components } from './api-types'

export type Source = components['schemas']['SourceView']
export type Item = components['schemas']['ItemView']
export type ItemDetail = components['schemas']['ItemDetail']
export type ItemPage = components['schemas']['Page_ItemView_']
export type FetchOutcome = components['schemas']['FetchOutcome']

const TOKEN_KEY = 'tentacles.token'
export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const saveToken = (token: string) => token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY)

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...init, headers })
  if (response.status === 401) {
    if (getToken() === token && window.location.pathname !== '/settings') window.location.assign('/settings?auth=required')
    throw new ApiError(401, '访问令牌无效或未设置，请在设置页更新。')
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const data = await response.json() as { error?: { message?: unknown } }
      if (typeof data.error?.message === 'string') message = data.error.message
    } catch { /* A proxy may return a non-JSON error; keep the safe status. */ }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

export function safeUrl(value: string): string | undefined {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}

export type Pipeline = components['schemas']['PipelineView']
export type PipelineOutcome = components['schemas']['PipelineOutcome']
export type Report = components['schemas']['ReportView']
export type ReportPage = components['schemas']['Page_ReportView_']
export type Run = components['schemas']['RunView']
export type Model = components['schemas']['ModelView']
export type ModelTest = components['schemas']['ModelTest']
export type Task = components['schemas']['TaskView']
export type SchedulerLoop = components['schemas']['SchedulerLoopView']
export type TickOutcome = components['schemas']['TickOutcome']
