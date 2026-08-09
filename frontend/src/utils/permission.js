/**
 * 权限工具模块
 * 
 * 提供前端权限判断工具函数和权限编码常量
 */

// ==========================================
// 权限编码常量（与后端 auth.py Perm 类对齐）
// ==========================================

export const Perm = {
  // 仪表盘
  DASHBOARD_VIEW: 'dashboard.view',

  // 数据库管理
  DATABASES_VIEW: 'databases.view',
  DATABASES_CREATE: 'databases.create',
  DATABASES_UPDATE: 'databases.update',
  DATABASES_DELETE: 'databases.delete',
  DATABASES_TEST_CONNECTION: 'databases.test_connection',
  DATABASES_TOGGLE_ACTIVE: 'databases.toggle_active',

  // 数据库详情
  DATABASE_DETAIL_VIEW: 'database_detail.view',

  // 监控指标
  METRICS_VIEW: 'metrics.view',

  // 基线分析
  BASELINE_VIEW: 'baseline.view',

  // 容量预测
  PREDICTION_VIEW: 'prediction.view',
  PREDICTION_EXECUTE: 'prediction.execute',

  // 健康评分
  HEALTH_VIEW: 'health.view',

  // 告警
  ALERTS_VIEW: 'alerts.view',
  ALERTS_ACKNOWLEDGE: 'alerts.acknowledge',
  ALERTS_DELETE: 'alerts.delete',

  // 告警配置
  ALERT_CONFIG_VIEW: 'alert_config.view',
  ALERT_CONFIG_MANAGE: 'alert_config.manage',

  // 工单
  TICKETS_VIEW: 'tickets.view',
  TICKETS_CREATE: 'tickets.create',
  TICKETS_APPROVE: 'tickets.approve',
  TICKETS_EXECUTE: 'tickets.execute',

  // SQL 监控
  SQL_MONITORING_VIEW: 'sql_monitoring.view',

  // 容量规划
  CAPACITY_VIEW: 'capacity.view',
  CAPACITY_PREDICT: 'capacity.predict',

  // 通知设置
  NOTIFICATION_VIEW: 'notification.view',
  NOTIFICATION_MANAGE: 'notification.manage',

  // 业务拓扑
  BUSINESS_TOPOLOGY_VIEW: 'business_topology.view',
  BUSINESS_TOPOLOGY_MANAGE: 'business_topology.manage',

  // 报表
  REPORTS_VIEW: 'reports.view',
  REPORTS_GENERATE: 'reports.generate',

  // 审计日志
  AUDIT_LOGS_VIEW: 'audit_logs.view',

  // 用户管理
  USERS_VIEW: 'users.view',
  USERS_MANAGE: 'users.manage',

  // 角色管理
  ROLES_VIEW: 'roles.view',
  ROLES_MANAGE: 'roles.manage',
};

// 菜单路由与权限的映射
export const MENU_PERMISSION_MAP = {
  '/': Perm.DASHBOARD_VIEW,
  '/databases': Perm.DATABASES_VIEW,
  '/alerts': Perm.ALERTS_VIEW,
  '/alert-config': Perm.ALERT_CONFIG_VIEW,
  '/sql-monitoring': Perm.SQL_MONITORING_VIEW,
  '/capacity': Perm.CAPACITY_VIEW,
  '/tickets': Perm.TICKETS_VIEW,
  '/notification-settings': Perm.NOTIFICATION_VIEW,
  '/business-systems': Perm.BUSINESS_TOPOLOGY_VIEW,
  '/reports': Perm.REPORTS_VIEW,
  '/user-management': Perm.USERS_VIEW,
};

// 路由与权限的映射（用于路由守卫）
export const ROUTE_PERMISSION_MAP = {
  '/': Perm.DASHBOARD_VIEW,
  '/databases': Perm.DATABASES_VIEW,
  '/databases/:id': Perm.DATABASE_DETAIL_VIEW,
  '/databases/:id/performance': Perm.METRICS_VIEW,
  '/alerts': Perm.ALERTS_VIEW,
  '/alert-config': Perm.ALERT_CONFIG_VIEW,
  '/capacity': Perm.CAPACITY_VIEW,
  '/tickets': Perm.TICKETS_VIEW,
  '/sql-monitoring': Perm.SQL_MONITORING_VIEW,
  '/ai-ops': Perm.METRICS_VIEW,
  '/notification-settings': Perm.NOTIFICATION_VIEW,
  '/business-systems': Perm.BUSINESS_TOPOLOGY_VIEW,
  '/reports': Perm.REPORTS_VIEW,
  '/system/health': Perm.DASHBOARD_VIEW,
  '/user-management': Perm.USERS_VIEW,
};

// ==========================================
// 权限判断函数
// ==========================================

/**
 * 获取当前用户的权限列表
 */
export function getUserPermissions() {
  const user = localStorage.getItem('user');
  if (!user) return [];
  try {
    const parsed = JSON.parse(user);
    return parsed.permissions || [];
  } catch {
    return [];
  }
}

/**
 * 获取当前用户的角色编码
 */
export function getUserRole() {
  const user = localStorage.getItem('user');
  if (!user) return null;
  try {
    const parsed = JSON.parse(user);
    return parsed.role || null;
  } catch {
    return null;
  }
}

/*
 * BUG-107: 以下判断此前都以 `getUserRole() === 'super_admin'` 短路放行。
 * 而 role 来自 localStorage —— 浏览器控制台一行
 *   localStorage.setItem('user', JSON.stringify({role:'super_admin'}))
 * 就能解锁全部菜单和路由。
 *
 * 现在一律只认服务端下发的 permissions 数组。这不会误伤超管：
 * 服务端 auth.get_user_permissions() 对 super_admin 直接返回全量权限清单
 * （见 monitor/auth.py），所以超管的 permissions 本就是完整的。
 *
 * 需要明确的是，前端鉴权只负责 UX（不显示够不到的入口）。
 * 真正的防线在后端 —— 篡改 localStorage 只会让按钮可见、请求 403。
 */

/**
 * 检查当前用户是否有某个权限
 */
export function hasPermission(permissionCode) {
  return getUserPermissions().includes(permissionCode);
}

/**
 * 检查当前用户是否有列表中任意一个权限（OR 语义）
 */
export function hasAnyPermission(permissionCodes) {
  const permissions = getUserPermissions();
  return permissionCodes.some(code => permissions.includes(code));
}

/**
 * 检查当前用户是否有列表中所有权限（AND 语义）
 */
export function hasAllPermissions(permissionCodes) {
  const permissions = getUserPermissions();
  return permissionCodes.every(code => permissions.includes(code));
}

/**
 * 根据权限过滤可见菜单
 */
export function getVisibleMenus() {
  const permSet = new Set(getUserPermissions());
  return Object.entries(MENU_PERMISSION_MAP)
    .filter(([_, perm]) => permSet.has(perm))
    .map(([path]) => path);
}

/**
 * 检查路由是否可访问
 */
export function canAccessRoute(pathname) {
  const permissions = getUserPermissions();
  const permSet = new Set(permissions);

  // 精确匹配
  if (ROUTE_PERMISSION_MAP[pathname]) {
    return permSet.has(ROUTE_PERMISSION_MAP[pathname]);
  }

  // 前缀匹配（如 /databases/5）
  for (const [route, perm] of Object.entries(ROUTE_PERMISSION_MAP)) {
    if (route.includes(':')) {
      const routePrefix = route.split('/:')[0];
      if (pathname.startsWith(routePrefix)) {
        return permSet.has(perm);
      }
    }
  }

  // 未在映射中的路由默认允许
  return true;
}
