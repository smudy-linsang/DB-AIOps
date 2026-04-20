import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { 
  Database, 
  AlertTriangle, 
  CheckCircle, 
  TrendingUp,
  Activity
} from 'lucide-react'
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer 
} from 'recharts'
import { getDatabaseList, getActiveAlerts, getMonitorLogs } from '../services/api'

function Dashboard() {
  const [databases, setDatabases] = useState([])
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    loadDashboardData()
  }, [])

  const loadDashboardData = async () => {
    try {
      setLoading(true)
      const [dbRes, alertRes] = await Promise.all([
        getDatabaseList(),
        getActiveAlerts()
      ])
      setDatabases(dbRes.data)
      setAlerts(alertRes.data)
      setError(null)
    } catch (err) {
      setError('Failed to load dashboard data')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const getStatusStats = () => {
    const healthy = databases.filter(db => db.status === 'healthy').length
    const warning = databases.filter(db => db.status === 'warning').length
    const error = databases.filter(db => db.status === 'error').length
    return { healthy, warning, error }
  }

  const stats = getStatusStats()

  if (loading) {
    return <div className="loading">Loading dashboard...</div>
  }

  if (error) {
    return <div className="error-message">{error}</div>
  }

  return (
    <div className="dashboard">
      <h1 className="page-title">Dashboard</h1>
      
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-card-title">
            <Database size={16} style={{ marginRight: 8, verticalAlign: 'middle' }} />
            Total Databases
          </div>
          <div className="stat-card-value">{databases.length}</div>
          <div className="stat-card-subtitle">Monitored instances</div>
        </div>
        
        <div className="stat-card">
          <div className="stat-card-title">
            <CheckCircle size={16} style={{ marginRight: 8, verticalAlign: 'middle', color: '#52c41a' }} />
            Healthy
          </div>
          <div className="stat-card-value" style={{ color: '#52c41a' }}>{stats.healthy}</div>
          <div className="stat-card-subtitle">Operating normally</div>
        </div>
        
        <div className="stat-card">
          <div className="stat-card-title">
            <AlertTriangle size={16} style={{ marginRight: 8, verticalAlign: 'middle', color: '#faad14' }} />
            Warnings
          </div>
          <div className="stat-card-value" style={{ color: '#faad14' }}>{stats.warning}</div>
          <div className="stat-card-subtitle">Need attention</div>
        </div>
        
        <div className="stat-card">
          <div className="stat-card-title">
            <Activity size={16} style={{ marginRight: 8, verticalAlign: 'middle', color: '#ff4d4f' }} />
            Active Alerts
          </div>
          <div className="stat-card-value" style={{ color: '#ff4d4f' }}>{alerts.length}</div>
          <div className="stat-card-subtitle">Require action</div>
        </div>
      </div>

      <div className="charts-grid">
        <div className="chart-card">
          <h3 className="chart-card-title">
            <TrendingUp size={16} style={{ marginRight: 8, verticalAlign: 'middle' }} />
            System Overview
          </h3>
          <div className="metric-chart">
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={[
                { time: '00:00', cpu: 45, memory: 62 },
                { time: '04:00', cpu: 38, memory: 58 },
                { time: '08:00', cpu: 65, memory: 72 },
                { time: '12:00', cpu: 78, memory: 85 },
                { time: '16:00', cpu: 72, memory: 80 },
                { time: '20:00', cpu: 55, memory: 68 },
              ]}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="time" />
                <YAxis />
                <Tooltip />
                <Line type="monotone" dataKey="cpu" stroke="#1890ff" strokeWidth={2} name="CPU %" />
                <Line type="monotone" dataKey="memory" stroke="#52c41a" strokeWidth={2} name="Memory %" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="chart-card">
          <h3 className="chart-card-title">Database Status</h3>
          <div style={{ display: 'flex', gap: 24, justifyContent: 'center', padding: '40px 0' }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ 
                width: 100, 
                height: 100, 
                borderRadius: '50%', 
                background: `conic-gradient(#52c41a ${stats.healthy/databases.length*360}deg, #e8e8e8 0deg)`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 16px'
              }}>
                <span style={{ fontSize: 24, fontWeight: 600 }}>{stats.healthy}</span>
              </div>
              <span style={{ color: '#52c41a', fontWeight: 500 }}>Healthy</span>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ 
                width: 100, 
                height: 100, 
                borderRadius: '50%', 
                background: `conic-gradient(#faad14 ${stats.warning/databases.length*360}deg, #e8e8e8 0deg)`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 16px'
              }}>
                <span style={{ fontSize: 24, fontWeight: 600 }}>{stats.warning}</span>
              </div>
              <span style={{ color: '#faad14', fontWeight: 500 }}>Warning</span>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ 
                width: 100, 
                height: 100, 
                borderRadius: '50%', 
                background: `conic-gradient(#ff4d4f ${stats.error/databases.length*360}deg, #e8e8e8 0deg)`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 16px'
              }}>
                <span style={{ fontSize: 24, fontWeight: 600 }}>{stats.error}</span>
              </div>
              <span style={{ color: '#ff4d4f', fontWeight: 500 }}>Error</span>
            </div>
          </div>
        </div>
      </div>

      {alerts.length > 0 && (
        <div className="chart-card" style={{ marginTop: 24 }}>
          <h3 className="chart-card-title">
            <AlertTriangle size={16} style={{ marginRight: 8, verticalAlign: 'middle', color: '#ff4d4f' }} />
            Recent Alerts
          </h3>
          <div className="alert-list">
            {alerts.slice(0, 5).map((alert, idx) => (
              <div key={idx} className="alert-item">
                <div className={`alert-icon ${alert.severity}`}>
                  <AlertTriangle size={20} />
                </div>
                <div className="alert-content">
                  <div className="alert-title">{alert.title}</div>
                  <div className="alert-time">{alert.created_at}</div>
                </div>
                <span className={`status-badge ${alert.severity}`}>
                  {alert.severity}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default Dashboard
