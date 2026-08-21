import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// DatabaseList.jsx 是前端改动最频繁的页面之一：列表拉取、状态/健康分/告警
// 聚合、名称搜索过滤都在这里。下面钉住两个最关键的行为：
// 1. databaseAPI.list() 返回的数据要真正渲染进表格；
// 2. 名称搜索框输入要实时过滤表格行。

vi.mock('../services/api', () => ({
  databaseAPI: {
    list: vi.fn(() =>
      Promise.resolve({
        databases: [
          { id: 1, name: '核心交易库', db_type: 'oracle', host: '10.0.0.1', port: 1521 },
          { id: 2, name: '报表库', db_type: 'mysql', host: '10.0.0.2', port: 3306 },
        ],
      })
    ),
    getStatus: vi.fn(() => Promise.resolve({ status: 'UP', metrics: {} })),
    getHealth: vi.fn(() => Promise.resolve({ scores: [] })),
  },
  alertAPI: {
    getByDatabase: vi.fn(() => Promise.resolve({ alerts: [] })),
  },
  // 模板列表失败走 catch 降级分支，不影响列表渲染
  collectTemplateAPI: {
    list: vi.fn(() => Promise.reject(new Error('templates unavailable'))),
  },
}))

// 权限守卫在测试中直接放行，聚焦列表本身的行为
vi.mock('../components/AuthGuard', () => ({
  PermissionGuard: ({ children }) => children,
}))

vi.mock('../utils/permission', () => ({
  Perm: new Proxy({}, { get: (_, key) => key }),
}))

import DatabaseList from '../pages/DatabaseList'

describe('DatabaseList 列表渲染与交互', () => {
  it('渲染数据库列表，并按名称搜索实时过滤表格行', async () => {
    render(
      <MemoryRouter>
        <DatabaseList />
      </MemoryRouter>
    )

    // 渲染：databaseAPI.list() 返回的两个库名进入表格
    expect(await screen.findByText('核心交易库', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText('报表库')).toBeInTheDocument()

    // 交互：输入搜索词后，不匹配的行被过滤掉
    fireEvent.change(screen.getByPlaceholderText('搜索数据库名称'), {
      target: { value: '核心' },
    })
    expect(screen.getByText('核心交易库')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('报表库')).not.toBeInTheDocument()
    })
  })
})
