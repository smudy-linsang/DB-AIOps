import axios from 'axios'

const API_BASE = '/api/v1'

// 创建 axios 实例
const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器 - 添加 Token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器 - 处理错误
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error) => {
    if (error.response?.status === 401) {
      // Token 过期或无效
      localStorage.removeItem('auth_token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// ==========================================
// 认证 API
// ==========================================

export const authAPI = {
  login: (username, password) => 
    api.post('/auth/login/', { username, password }),
  logout: () => 
    api.post('/auth/logout/'),
  getCurrentUser: () => 
    api.get('/users/me/'),
}

// ==========================================
// 健康检查 API
// ==========================================

export const healthAPI = {
  check: () => api.get('/health/'),
}

// ==========================================
// 数据库配置 API
// ==========================================

export const databaseAPI = {
  // 获取数据库列表
  list: () => api.get('/databases/'),
  
  // 创建数据库配置
  create: (data) => api.post('/databases/', data),
  
  // 获取数据库详情
  getDetail: (id) => api.get(`/databases/${id}/`),
  
  // 获取数据库状态
  getStatus: (id) => api.get(`/databases/${id}/status/`),
  
  // 获取数据库指标
  getMetrics: (id, params = {}) => 
    api.get(`/databases/${id}/metrics/`, { params }),
  
  // 获取基线
  getBaseline: (id) => api.get(`/databases/${id}/baseline/`),
  
  // 获取预测
  getPrediction: (id) => api.get(`/databases/${id}/prediction/`),
  
  // 获取健康评分
  getHealth: (id) => api.get(`/databases/${id}/health/`),
  
  // 获取统计信息
  getStats: () => api.get('/databases/stats/'),
}

// ==========================================
// 告警 API
// ==========================================

export const alertAPI = {
  // 获取告警列表
  list: (params = {}) => api.get('/alerts/', { params }),
  
  // 获取告警统计
  getStatistics: () => api.get('/alerts/statistics/'),
  
  // 确认告警
  acknowledge: (id) => api.post(`/alerts/${id}/acknowledge/`),
  
  // 获取数据库关联告警
  getByDatabase: (dbId) => api.get(`/databases/${dbId}/alerts/`),
}

// ==========================================
// 运维工单 API
// ==========================================

export const auditLogAPI = {
  // 获取工单列表
  list: (params = {}) => api.get('/auditlogs/', { params }),
  
  // 审批工单
  approve: (id) => api.post(`/auditlogs/${id}/approve/`),
  
  // 拒绝工单
  reject: (id, reason) => api.post(`/auditlogs/${id}/reject/`, { reason }),
  
  // 执行工单
  execute: (id) => api.post(`/auditlogs/${id}/execute/`),
  
  // 预执行（验证SQL语法）
  dryRun: (id) => api.post(`/auditlogs/${id}/dry-run/`),
}

// ==========================================
// 用户管理 API
// ==========================================

export const userAPI = {
  // 获取用户列表
  list: () => api.get('/users/'),
  
  // 获取用户详情
  getDetail: (id) => api.get(`/users/${id}/`),
  
  // 更新用户
  update: (id, data) => api.put(`/users/${id}/`, data),
  
  // 修改密码
  changePassword: (id, password) => 
    api.put(`/users/${id}/password/`, { password }),
  
  // 获取当前用户
  getCurrentUser: () => api.get('/users/me/'),
}

// ==========================================
// 仪表盘 API
// ==========================================

export const dashboardAPI = {
  // 获取仪表盘统计
  getStats: () => api.get('/dashboard/stats/'),
  
  // 获取仪表盘图表数据
  getCharts: () => api.get('/dashboard/charts/'),
  
  // 获取健康评分趋势
  getHealthTrend: (days = 7) => 
    api.get('/dashboard/health-trend/', { params: { days } }),
  
  // 获取告警趋势
  getAlertTrend: (days = 7) => 
    api.get('/dashboard/alert-trend/', { params: { days } }),
}

// ==========================================
// 便捷函数
// ==========================================

export const setAuthToken = (token) => {
  localStorage.setItem('auth_token', token)
}

export const clearAuthToken = () => {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('user')
}

export const getAuthToken = () => {
  return localStorage.getItem('auth_token')
}

export const setUser = (user) => {
  localStorage.setItem('user', JSON.stringify(user))
}

export const getUser = () => {
  const userStr = localStorage.getItem('user')
  return userStr ? JSON.parse(userStr) : null
}

export const isAuthenticated = () => {
  return !!localStorage.getItem('auth_token')
}

export default api
