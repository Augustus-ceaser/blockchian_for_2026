import type { DemoRunResponse } from './types'
import { responseErrorMessage } from './errorDetail'

const configuredBase = import.meta.env.VITE_API_BASE_URL || '/api/v1'
export const apiBaseUrl = configuredBase.replace(/\/$/, '')

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new ApiError(
      response.status,
      await responseErrorMessage(response, `请求失败（${response.status}）`),
    )
  }
  return response.json() as Promise<T>
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return decode<T>(await fetch(`${apiBaseUrl}${path}`, { signal }))
}

export async function startPathmnistRun(idempotencyKey: string): Promise<DemoRunResponse> {
  return decode<DemoRunResponse>(
    await fetch(`${apiBaseUrl}/demo/pathmnist/runs`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
        'X-Demo-Role': 'ai_company',
      },
      body: JSON.stringify({ scenario: 'pathmnist_resnet18_20' }),
    }),
  )
}
