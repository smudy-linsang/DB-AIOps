import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  Perm,
  hasPermission,
  hasAnyPermission,
  hasAllPermissions,
  getVisibleMenus,
  canAccessRoute,
  getUserPermissions,
  getUserRole,
} from '../utils/permission'

describe('permission 工具函数', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  describe('getUserPermissions / getUserRole', () => {
    it('无用户数据时返回空数组和 null', () => {
      expect(getUserPermissions()).toEqual([])
      expect(getUserRole()).toBeNull()
    })

    it('从 localStorage 解析 permissions 和 role', () => {
      localStorage.setItem('user', JSON.stringify({
        role: 'dba',
        permissions: ['dashboard.view', 'databases.view'],
      }))

      expect(getUserPermissions()).toEqual(['dashboard.view', 'databases.view'])
      expect(getUserRole()).toBe('dba')
    })

    it('JSON 解析失败时降级为空数组/null', () => {
      localStorage.setItem('user', '{invalid json')
      expect(getUserPermissions()).toEqual([])
      expect(getUserRole()).toBeNull()
    })
  })

  describe('hasPermission', () => {
    it('有权限时返回 true', () => {
      localStorage.setItem('user', JSON.stringify({
        permissions: [Perm.DASHBOARD_VIEW, Perm.DATABASES_VIEW],
      }))

      expect(hasPermission(Perm.DASHBOARD_VIEW)).toBe(true)
      expect(hasPermission(Perm.DATABASES_DELETE)).toBe(false)
    })

    it('无用户数据时所有权限检查返回 false', () => {
      expect(hasPermission(Perm.DASHBOARD_VIEW)).toBe(false)
    })
  })

  describe('hasAnyPermission (OR 语义)', () => {
    it('有其中任意一个即返回 true', () => {
      localStorage.setItem('user', JSON.stringify({
        permissions: [Perm.DATABASES_VIEW],
      }))

      expect(hasAnyPermission([Perm.DATABASES_VIEW, Perm.DATABASES_DELETE])).toBe(true)
      expect(hasAnyPermission([Perm.DATABASES_DELETE, Perm.ALERTS_VIEW])).toBe(false)
    })
  })

  describe('hasAllPermissions (AND 语义)', () => {
    it('需要全部拥有才返回 true', () => {
      localStorage.setItem('user', JSON.stringify({
        permissions: [Perm.DATABASES_VIEW, Perm.DATABASES_CREATE],
      }))

      expect(hasAllPermissions([Perm.DATABASES_VIEW, Perm.DATABASES_CREATE])).toBe(true)
      expect(hasAllPermissions([Perm.DATABASES_VIEW, Perm.DATABASES_DELETE])).toBe(false)
    })
  })

  describe('getVisibleMenus', () => {
    it('根据权限过滤可见菜单路径', () => {
      localStorage.setItem('user', JSON.stringify({
        permissions: [Perm.DASHBOARD_VIEW, Perm.DATABASES_VIEW],
      }))

      const visible = getVisibleMenus()
      expect(visible).toContain('/')
      expect(visible).toContain('/databases')
      expect(visible).not.toContain('/alerts')
    })
  })

  describe('canAccessRoute', () => {
    it('有对应权限的路由返回 true', () => {
      localStorage.setItem('user', JSON.stringify({
        permissions: [Perm.DASHBOARD_VIEW, Perm.DATABASES_VIEW, Perm.DATABASE_DETAIL_VIEW],
      }))

      expect(canAccessRoute('/')).toBe(true)
      expect(canAccessRoute('/databases')).toBe(true)
      expect(canAccessRoute('/databases/5')).toBe(true)
    })

    it('缺少权限的路由返回 false', () => {
      localStorage.setItem('user', JSON.stringify({
        permissions: [Perm.DASHBOARD_VIEW],
      }))

      expect(canAccessRoute('/databases')).toBe(false)
      expect(canAccessRoute('/alerts')).toBe(false)
    })

    it('未在映射中的路由默认允许', () => {
      expect(canAccessRoute('/unknown-path')).toBe(true)
    })
  })
})
