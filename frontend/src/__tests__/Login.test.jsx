import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Mock services/api：Login 组件导入了 authAPI/setAuthToken/setUser，
// 但渲染阶段不触发网络请求，只需保证模块可解析即可。
vi.mock('../services/api', () => ({
  authAPI: {
    login: vi.fn(),
    getCurrentUser: vi.fn(),
  },
  setAuthToken: vi.fn(),
  setUser: vi.fn(),
  default: {
    authAPI: { login: vi.fn(), getCurrentUser: vi.fn() },
    setAuthToken: vi.fn(),
    setUser: vi.fn(),
  },
}))

import Login from '../pages/Login'

describe('Login 页面冒烟测试', () => {
  it('渲染登录表单并显示标题', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )

    expect(screen.getByText('DB Monitor')).toBeInTheDocument()
    expect(screen.getByText('数据库智能监控系统')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('密码')).toBeInTheDocument()
  })
})
