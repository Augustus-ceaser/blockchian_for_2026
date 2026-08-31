import type { DemoIdentity } from './types'
import { responseErrorMessage } from '../api/errorDetail'

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const baseUrl = configuredBase.replace(/\/$/, '')

export class RoadshowApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new RoadshowApiError(
      response.status,
      await responseErrorMessage(response, `请求失败（${response.status}）`),
    )
  }
  return response.json() as Promise<T>
}

export async function platformGet<T>(
  path: string,
  _identity: DemoIdentity,
  signal?: AbortSignal,
): Promise<T> {
  return decode<T>(await fetch(`${baseUrl}${path}`, {
    signal,
    credentials: 'include',
  }))
}

export async function roadshowGet<T>(path: string, identity: DemoIdentity, signal?: AbortSignal): Promise<T> {
  return platformGet<T>(`/roadshow${path}`, identity, signal)
}

export async function platformCommand<T>(
  path: string,
  _identity: DemoIdentity,
  idempotencyKey: string,
  body?: unknown,
  method: 'POST' | 'PATCH' = 'POST',
): Promise<T> {
  return decode<T>(await fetch(`${baseUrl}${path}`, {
    method,
    credentials: 'include',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  }))
}

export async function roadshowCommand<T>(
  path: string,
  identity: DemoIdentity,
  idempotencyKey: string,
  body?: unknown,
): Promise<T> {
  return platformCommand<T>(`/roadshow${path}`, identity, idempotencyKey, body)
}

export async function roadshowDownload(
  _identity: DemoIdentity,
  token: string,
  idempotencyKey: string,
): Promise<Blob> {
  const response = await fetch(`${baseUrl}/roadshow/result-downloads`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'X-Download-Token': token,
      'Idempotency-Key': idempotencyKey,
    },
  })
  if (!response.ok) {
    throw new RoadshowApiError(
      response.status,
      await responseErrorMessage(response, `下载失败（${response.status}）`),
    )
  }
  return response.blob()
}

export async function platformDownload(
  _identity: DemoIdentity,
  token: string,
  idempotencyKey: string,
): Promise<Blob> {
  const response = await fetch(`${baseUrl}/result-downloads`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'X-Download-Token': token,
      'Idempotency-Key': idempotencyKey,
    },
  })
  if (!response.ok) {
    throw new RoadshowApiError(
      response.status,
      await responseErrorMessage(response, `下载失败（${response.status}）`),
    )
  }
  return response.blob()
}

export async function authLogin(username: string, password: string): Promise<void> {
  await decode(await fetch(`${baseUrl}/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }))
}

export async function authMe<T>(signal?: AbortSignal): Promise<T> {
  return decode<T>(await fetch(`${baseUrl}/auth/me`, {
    signal,
    credentials: 'include',
  }))
}

export async function authLogout(): Promise<void> {
  const response = await fetch(`${baseUrl}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!response.ok && response.status !== 401) {
    await decode(response)
  }
}

export type DeploymentStatus = {
  mode: 'local' | 'lan-roadshow' | 'remote-preview' | 'production-template'
  label: string
  join_enabled: boolean
  public_origin: string
  gateway_port: number
  hard_isolation: boolean
  executor: string
}

export async function deploymentStatus(signal?: AbortSignal): Promise<DeploymentStatus> {
  return decode<DeploymentStatus>(await fetch(`${baseUrl}/health/deployment`, { signal }))
}
