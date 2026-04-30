import React, { useState, useEffect } from 'react'
import { Routes, Route, Link, Navigate, useNavigate } from 'react-router-dom'
import { Layout, Menu, Dropdown, Avatar, Space, Typography } from 'antd'
import {
  DashboardOutlined,
  DatabaseOutlined,
  BellOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  FundOutlined,
  FileTextOutlined
} from '@ant-design/icons'
import Dashboard from './pages/Dashboard'
import DatabaseList from './pages/DatabaseList'
import DatabaseDetail from './pages/DatabaseDetail'
import AlertList from './pages/AlertList'
import CapacityPlanning from './pages/CapacityPlanning'
import TicketManagement from './pages/TicketManagement'
import Login from './pages/Login'
import { isAuthenticated, getUser, clearAuthToken, authAPI } from './services/api'

const { Header, Content } = Layout
const { Text } = Typography

// 认证布局
const AuthenticatedLayout = ({ children }) => {
  const navigate = useNavigate()
  const [user, setUserData] = useState(getUser())

  const handleLogout = async () => {
    try {
      await authAPI.logout()
    } catch (e) {
      // 忽略登出错误
    }
    clearAuthToken()
    navigate('/login')
  }

  const userMenu = {
    items: [
      {
        key: 'profile',
        icon: <UserOutlined />,
        label: '个人中心'
      },
      {
        key: 'settings',
        icon: <SettingOutlined />,
        label: '设置'
      },
      { type: 'divider' },
      {
        key: 'logout',
        icon: <LogoutOutlined />,
        label: '退出登录',
        danger: true
      }
    ],
    onClick: ({ key }) => {
      if (key === 'logout') {
        handleLogout()
      }
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ 
        display: 'flex', 
        alignItems: 'center',
        justifyContent: 'space-between',
        background: '#001529',
        padding: '0 24px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <div style={{ color: 'white', fontSize: 18, fontWeight: 'bold', marginRight: 32 }}>
            DB Monitor
          </div>
          <Menu
            theme="dark"
            mode="horizontal"
            defaultSelectedKeys={['dashboard']}
            items={[
              { key: 'dashboard', icon: <DashboardOutlined />, label: <Link to="/">仪表盘</Link> },
              { key: 'databases', icon: <DatabaseOutlined />, label: <Link to="/databases">数据库</Link> },
              { key: 'alerts', icon: <BellOutlined />, label: <Link to="/alerts">告警</Link> },
              { key: 'capacity', icon: <FundOutlined />, label: <Link to="/capacity">容量规划</Link> },
              { key: 'tickets', icon: <FileTextOutlined />, label: <Link to="/tickets">工单</Link> }
            ]}
            style={{ flex: 1, minWidth: 0 }}
          />
        </div>
        
        <Dropdown menu={userMenu} placement="bottomRight">
          <Space style={{ cursor: 'pointer' }}>
            <Avatar icon={<UserOutlined />} size="small" />
            <Text style={{ color: 'white' }}>{user?.username || 'User'}</Text>
          </Space>
        </Dropdown>
      </Header>
      
      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        {children}
      </Content>
    </Layout>
  )
}

// 路由守卫
const PrivateRoute = ({ children }) => {
  return isAuthenticated() ? children : <Navigate to="/login" replace />
}

// 主应用
function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <AuthenticatedLayout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/databases" element={<DatabaseList />} />
                <Route path="/databases/:id" element={<DatabaseDetail />} />
                <Route path="/alerts" element={<AlertList />} />
                <Route path="/capacity" element={<CapacityPlanning />} />
                <Route path="/tickets" element={<TicketManagement />} />
              </Routes>
            </AuthenticatedLayout>
          </PrivateRoute>
        }
      />
    </Routes>
  )
}

export default App
