import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import useAppStore from '../stores/useAppStore'

let connections

beforeEach(() => {
  connections = []
  vi.useFakeTimers()
  vi.stubGlobal('EventSource', class extends EventTarget {
    close = vi.fn()
    constructor() {
      super()
      connections.push(this)
    }
  })
})

afterEach(() => {
  useAppStore.getState().disconnectSSE()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it('closes a server-degraded stream and ignores the subsequent error', () => {
  useAppStore.getState().connectSSE()
  const stream = connections[0]
  stream.dispatchEvent(new MessageEvent('degraded', { data: JSON.stringify({ message: 'Redis 未启用' }) }))
  stream.onerror()
  vi.advanceTimersByTime(60000)

  expect(useAppStore.getState().sseUnavailableReason).toBe('Redis 未启用')
  expect(useAppStore.getState().sseConnected).toBe(false)
  expect(stream.close).toHaveBeenCalledOnce()
  expect(connections).toHaveLength(1)
})

it('retries a temporary failure and cancels a pending retry on logout', () => {
  useAppStore.getState().connectSSE()
  connections[0].onerror()
  vi.advanceTimersByTime(1000)
  expect(connections).toHaveLength(2)
  connections[1].dispatchEvent(new Event('connected'))
  expect(useAppStore.getState().sseConnected).toBe(true)

  connections[1].onerror()
  useAppStore.getState().disconnectSSE()
  vi.advanceTimersByTime(60000)
  expect(connections).toHaveLength(2)
  expect(useAppStore.getState().sseConnection).toBeNull()
})
