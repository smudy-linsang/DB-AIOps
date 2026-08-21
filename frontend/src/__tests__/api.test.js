import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// api.js 是改动最频繁的热点文件：Token 注入、401 强制登出、5xx 幂等重试、
// 双错误契约归一化（BUG-116/BUG-117）全部集中在 axios 拦截器里，
// 任何一处改坏都会静默影响全站所有页面的请求行为。
// 这里用一个最小 axios 桩驱动真实拦截器管线（请求拦截器 -> 传输层 ->
// 响应拦截器），把核心请求分支钉成可失败、可定位的用例。

const mockState = vi.hoisted(() => ({ requestFn: null }))

vi.mock('axios', () => {
  // 与 axios 语义对齐：请求拦截器正序加工 config，传输层 settle 后
  // 响应拦截器正序处理，rejected 交给 onRejected，任一环抛错即转为拒绝。
  async function dispatch(client, config, reqHandlers, resHandlers) {
    let cfg = config
    for (const fn of reqHandlers) cfg = await fn(cfg)
    let settled
    try {
      settled = { ok: true, value: await mockState.requestFn(cfg) }
    } catch (e) {
      settled = { ok: false, value: e }
    }
    for (const { onFulfilled, onRejected } of resHandlers) {
      try {
        // axios 语义：onRejected 正常返回（含重试成功）即恢复为 fulfilled
        settled = settled.ok
          ? { ok: true, value: await onFulfilled(settled.value) }
          : { ok: true, value: await onRejected(settled.value) }
      } catch (e) {
        settled = { ok: false, value: e }
      }
    }
    if (!settled.ok) throw settled.value
    return settled.value
  }

  return {
    default: {
      create: () => {
        const reqHandlers = []
        const resHandlers = []
        // 对齐 axios.create：config 总是带默认 headers 对象
        const client = (config) => dispatch(client, { headers: {}, ...config }, reqHandlers, resHandlers)
        client.get = (url, cfg = {}) => client({ ...cfg, url, method: 'get' })
        client.post = (url, data, cfg = {}) => client({ ...cfg, url, data, method: 'post' })
        client.interceptors = {
          request: { use: (fn) => reqHandlers.push(fn) },
          response: { use: (onFulfilled, onRejected) => resHandlers.push({ onFulfilled, onRejected }) },
        }
        return client
      },
    },
  }
})

import { databaseAPI } from '../services/api'

describe('api.js 核心请求分支', () => {
  beforeEach(() => {
    mockState.requestFn = vi.fn()
    window.history.pushState({}, '', '/')
  })

  afterEach(() => {
    vi.useRealTimers()
    window.history.pushState({}, '', '/')
  })

  it('请求注入 Bearer Token，成功时直接解包 response.data', async () => {
    localStorage.setItem('auth_token', 'tok-123')
    mockState.requestFn.mockResolvedValue({ status: 200, data: { databases: [{ id: 1 }] } })

    await expect(databaseAPI.list()).resolves.toEqual({ databases: [{ id: 1 }] })
    expect(mockState.requestFn.mock.calls[0][0].headers.Authorization).toBe('Bearer tok-123')
  })

  it('非幂等请求 5xx 不重试，错误归一化保留后端 {code, message} 契约', async () => {
    mockState.requestFn.mockRejectedValue({
      config: { method: 'post' },
      response: { status: 502, data: { code: 'TSDB_DOWN', message: '时序库不可用' } },
    })

    // 旧实现只读 data.error，这里会退化成 "Request failed with status code 502"
    await expect(databaseAPI.create({ name: 'x' })).rejects.toMatchObject({
      message: '时序库不可用',
      code: 'TSDB_DOWN',
      status: 502,
    })
    expect(mockState.requestFn).toHaveBeenCalledTimes(1)
  })

  it('GET 遇 5xx 自动重试一次，重试成功后正常返回', async () => {
    vi.useFakeTimers()
    mockState.requestFn
      .mockRejectedValueOnce({
        config: { method: 'get' },
        response: { status: 502, data: { error: 'bad gateway' } },
      })
      .mockResolvedValueOnce({ status: 200, data: { databases: [] } })

    const promise = databaseAPI.list()
    await vi.advanceTimersByTimeAsync(1000)

    await expect(promise).resolves.toEqual({ databases: [] })
    expect(mockState.requestFn).toHaveBeenCalledTimes(2)
  })

  it('401 清除本地凭据并拒绝原始错误（登录页不重复跳转）', async () => {
    localStorage.setItem('auth_token', 'tok-123')
    localStorage.setItem('user', '{"username":"admin"}')
    window.history.pushState({}, '', '/login')
    mockState.requestFn.mockRejectedValue({
      config: { method: 'get' },
      response: { status: 401, data: { error: '未授权' } },
    })

    await expect(databaseAPI.list()).rejects.toMatchObject({ response: { status: 401 } })
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
  })
})
