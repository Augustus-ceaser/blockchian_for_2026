export type AbortableLoadHandlers<T> = {
  onSuccess: (value: T) => void
  onError: (error: unknown) => void
  onSettled?: () => void
}

export function isRequestAbort(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const candidate = error as { name?: string; code?: string }
  return candidate.name === 'AbortError'
    || candidate.name === 'CanceledError'
    || candidate.code === 'ERR_CANCELED'
}

export function startAbortableLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  handlers: AbortableLoadHandlers<T>,
): () => void {
  const controller = new AbortController()
  let active = true

  void load(controller.signal)
    .then((value) => {
      if (active) handlers.onSuccess(value)
    })
    .catch((error: unknown) => {
      if (active && !isRequestAbort(error)) handlers.onError(error)
    })
    .finally(() => {
      if (active) handlers.onSettled?.()
    })

  return () => {
    active = false
    controller.abort()
  }
}

export function createSingleFlight() {
  let active = false

  return {
    async run<T>(operation: () => Promise<T>): Promise<
      { started: true; value: T } | { started: false }
    > {
      if (active) return { started: false }
      active = true
      try {
        return { started: true, value: await operation() }
      } finally {
        active = false
      }
    },
  }
}
