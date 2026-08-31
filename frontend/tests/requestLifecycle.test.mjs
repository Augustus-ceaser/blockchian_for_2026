import assert from 'node:assert/strict'
import test from 'node:test'
import { createSingleFlight, startAbortableLoad } from '../src/roadshow/requestLifecycle.ts'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
}

test('active cancellation is not surfaced as an error', async () => {
  let errorCount = 0
  const cancel = startAbortableLoad(
    (signal) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(
        new DOMException('aborted', 'AbortError'),
      ))
    }),
    { onSuccess() {}, onError() { errorCount += 1 } },
  )

  cancel()
  await flushPromises()

  assert.equal(errorCount, 0)
})

test('ordinary network errors remain visible', async () => {
  let capturedError
  startAbortableLoad(
    async () => {
      throw new TypeError('Failed to fetch')
    },
    { onSuccess() {}, onError(error) { capturedError = error } },
  )

  await flushPromises()

  assert.ok(capturedError instanceof TypeError)
  assert.equal(capturedError.message, 'Failed to fetch')
})

test('late responses do not update an unmounted consumer', async () => {
  const request = deferred()
  let successCount = 0
  let settledCount = 0
  const cancel = startAbortableLoad(
    () => request.promise,
    {
      onSuccess() { successCount += 1 },
      onError() {},
      onSettled() { settledCount += 1 },
    },
  )

  cancel()
  request.resolve('late response')
  await flushPromises()

  assert.equal(successCount, 0)
  assert.equal(settledCount, 0)
})

test('pending writes are submitted only once', async () => {
  const request = deferred()
  let operationCount = 0
  const guard = createSingleFlight()
  const operation = () => {
    operationCount += 1
    return request.promise
  }

  const first = guard.run(operation)
  const second = await guard.run(operation)

  assert.deepEqual(second, { started: false })
  assert.equal(operationCount, 1)

  request.resolve('ok')
  assert.deepEqual(await first, { started: true, value: 'ok' })
})
