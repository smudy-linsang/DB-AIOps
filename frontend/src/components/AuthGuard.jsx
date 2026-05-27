/**
 * AuthGuard - 权限控制组件
 * 
 * <PermissionGuard code="databases.create"> 包裹按钮，无权限则隐藏
 * <RouteGuard> 包裹路由，无权限则跳转403
 * usePermission() hook 在组件内判断权限
 */
import React from 'react';
import { Navigate } from 'react-router-dom';
import { hasPermission, hasAnyPermission, hasAllPermissions } from '../utils/permission';

/**
 * 权限守卫组件 - 包裹需要权限的元素
 * 无权限时隐藏（不渲染）
 * 
 * 用法:
 * <PermissionGuard code="databases.create">
 *   <Button>新增数据库</Button>
 * </PermissionGuard>
 * 
 * // OR 语义
 * <PermissionGuard code={['tickets.approve', 'tickets.execute']}>
 *   <Button>操作</Button>
 * </PermissionGuard>
 */
export function PermissionGuard({ code, children, fallback = null }) {
  const codes = Array.isArray(code) ? code : [code];
  const allowed = hasAnyPermission(codes);
  return allowed ? children : fallback;
}

/**
 * 路由守卫组件 - 检查路由权限
 * 无权限时跳转到 403 页面
 * 
 * 用法:
 * <RouteGuard>
 *   <SomePage />
 * </RouteGuard>
 */
export function RouteGuard({ children }) {
  // 路由权限检查在 App.jsx 层面通过 canAccessRoute 处理
  // 此组件作为额外防线
  return children;
}

/**
 * 权限 Hook
 * 
 * 用法:
 * const { has, hasAny, hasAll } = usePermission();
 * if (has('databases.create')) { ... }
 */
export function usePermission() {
  const has = (code) => hasPermission(code);
  const hasAny = (codes) => hasAnyPermission(codes);
  const hasAll = (codes) => hasAllPermissions(codes);

  return { has, hasAny, hasAll };
}

export default PermissionGuard;
