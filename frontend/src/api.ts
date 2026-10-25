import type { components, paths } from './api-types'

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

type Method<Path extends keyof paths> = {
  [Key in keyof paths[Path]]-?: paths[Path][Key] extends { responses: unknown } ? Key : never
}[keyof paths[Path]] & string
type JsonContent<Value> = Value extends { content: { 'application/json': infer Data } } ? Data : never
type Parameters<Operation> = Operation extends { parameters: infer Params }
  ? Pick<Params, Extract<keyof Params, 'path' | 'query'>> : never
type Body<Operation> = Operation extends { requestBody?: infer Value }
  ? Operation extends { requestBody: unknown } ? { body: JsonContent<Value> } : { body?: JsonContent<Value> }
  : { body?: never }
type Options<Operation> = Omit<RequestInit, 'method' | 'body'> & Parameters<Operation> & Body<Operation>
type Arguments<Operation> = {} extends Options<Operation> ? [options?: Options<Operation>] : [options: Options<Operation>]
type Success<Operation> = Operation extends { responses: infer Responses } ? {
  [Status in keyof Responses]: Status extends string | number
    ? `${Status}` extends `2${string}` ? JsonContent<Responses[Status]> : never : never
}[keyof Responses] : never

type QueryValue = string | number | boolean | null | undefined
type RuntimeOptions = Omit<RequestInit, 'method' | 'body'> & {
  path?: Record<string, string | number>
  query?: Record<string, QueryValue | readonly QueryValue[]>
  body?: unknown
}

// Only the generated path and method select the contract; request options must
// satisfy it, not widen inference to a different operation. JSON is trusted at
// this single transport boundary, never asserted by individual callers.
export function api<Path extends keyof paths, Verb extends Method<Path>>(
  path: Path, method: Verb, ...args: Arguments<paths[NoInfer<Path>][NoInfer<Verb>]>
): Promise<Success<paths[Path][Verb]>>
export async function api(template: string, method: string, options: RuntimeOptions = {}): Promise<unknown> {
  const { path: params, query, body, ...init } = options
  const path = template.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = params?.[name]
    if (value === undefined) throw new Error(`Missing path parameter: ${name}`)
    return encodeURIComponent(value)
  })
  const search = new URLSearchParams()
  for (const [name, value] of Object.entries(query || {})) {
    // OpenAPI form/explode: arrays are repeated keys, not comma-joined strings.
    for (const entry of Array.isArray(value) ? value : [value]) {
      if (entry != null) search.append(name, String(entry))
    }
  }
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${path}${search.size ? `?${search}` : ''}`, {
    ...init, method: method.toUpperCase(), headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401) {
    if (getToken() === token && window.location.pathname !== '/settings') window.location.assign('/settings?auth=required')
    throw new ApiError(401, '访问令牌无效或未设置，请在设置页更新。')
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const data: unknown = await response.json()
      if (data && typeof data === 'object' && 'error' in data) {
        const error = data.error
        if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') message = error.message
      }
    } catch { /* A proxy may return a non-JSON error; keep the safe status. */ }
    throw new ApiError(response.status, message)
  }
  return response.json()
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

export type Persona = components['schemas']['PersonaView']
export type PersonaProposal = components['schemas']['PersonaProposal']
export type Feedback = components['schemas']['FeedbackView']
