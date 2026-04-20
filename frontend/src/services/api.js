import axios from 'axios'

const API_BASE = '/api'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Database Config API
export const getDatabaseList = () => api.get('/configs/')
export const getDatabaseDetail = (id) => api.get(`/configs/${id}/`)
export const createDatabase = (data) => api.post('/configs/', data)
export const updateDatabase = (id, data) => api.put(`/configs/${id}/`, data)
export const deleteDatabase = (id) => api.delete(`/configs/${id}/`)

// Monitor Logs API
export const getMonitorLogs = (configId, params) => 
  api.get(`/logs/${configId}/`, { params })
export const getLatestMetrics = (configId) => 
  api.get(`/metrics/${configId}/latest/`)

// Intelligent Baseline API
export const getIntelligentBaseline = (configId) => 
  api.get(`/baseline/${configId}/intelligent/`)
export const getBaselineTrend = (configId) => 
  api.get(`/baseline/${configId}/trend/`)

// Anomaly Detection API
export const getAnomalyDetection = (configId) => 
  api.get(`/anomaly/${configId}/`)

// RCA API
export const getRCAnalysis = (configId) => 
  api.get(`/rca/${configId}/`)

// Capacity Prediction API
export const getCapacityPrediction = (configId, metric, days) => 
  api.get(`/capacity/${configId}/predict/`, { 
    params: { metric, days } 
  })

// Alert API
export const getActiveAlerts = () => api.get('/alerts/active/')
export const getAlertHistory = (params) => api.get('/alerts/history/', { params })
export const getAlerts = (filters) => api.get('/alerts/', { params: filters })
export const getAlertStatistics = () => api.get('/alerts/statistics/')
export const acknowledgeAlert = (alertId) => api.post(`/alerts/${alertId}/acknowledge/`)

// Dashboard API
export const getDashboardStats = () => api.get('/dashboard/stats/')
export const getDashboardCharts = () => api.get('/dashboard/charts/')

// Database List API
export const getDatabases = () => api.get('/databases/')
export const getDatabaseStats = () => api.get('/databases/stats/')

// Database Metrics API
export const getDatabaseMetrics = (id, timeRange) => api.get(`/databases/${id}/metrics/`, { 
  params: { time_range: timeRange } 
})
export const getDatabaseAlerts = (id) => api.get(`/databases/${id}/alerts/`)

// Remediation API
export const getPendingOperations = () => api.get('/remediation/pending/')
export const approveOperation = (auditId, approver) => 
  api.post(`/remediation/${auditId}/approve/`, { approver })
export const rejectOperation = (auditId, reason) => 
  api.post(`/remediation/${auditId}/reject/`, { reason })

// Health Check API
export const healthCheck = () => api.get('/health/')

export default api
