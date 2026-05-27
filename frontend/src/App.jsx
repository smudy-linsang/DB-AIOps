import React, { useEffect, useState } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { ConfigProvider, App as AntApp, Result, Button, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import EMLayout from './components/EMLayout';
import ErrorBoundary from './components/ErrorBoundary';
import Dashboard from './pages/Dashboard';
import DatabaseList from './pages/DatabaseList';
import DatabaseDetail from './pages/DatabaseDetail';
import DatabasePerformanceHub from './pages/DatabasePerformanceHub';
import AlertList from './pages/AlertList';
import AlertConfig from './pages/AlertConfig';
import CapacityPlanning from './pages/CapacityPlanning';
import TicketManagement from './pages/TicketManagement';
import SQLMonitoring from './pages/SQLMonitoring';
import NotificationSettings from './pages/NotificationSettings';
import BusinessSystems from './pages/BusinessSystems';
import ReportList from './pages/ReportList';
import Login from './pages/Login';
import UserManagement from './pages/UserManagement';
import { canAccessRoute, getUserRole } from './utils/permission';
import { authAPI, setUser as saveUserToStorage } from './services/api';

const TOKEN_KEY = 'auth_token';

// 私密路由包装器
function PrivateRoute({ children }) {
  const token = localStorage.getItem(TOKEN_KEY);
  const location = useLocation();
  const [permReady, setPermReady] = useState(() => {
    // 检查权限数据是否已就绪（兼容旧版本 localStorage 数据）
    const user = localStorage.getItem('user') ? JSON.parse(localStorage.getItem('user')) : null;
    return !user || (user.permissions && user.role);
  });

  useEffect(() => {
    if (permReady) return;
    // 旧版本数据缺少权限字段，异步刷新
    authAPI.getCurrentUser().then(res => {
      const userData = res.data || res;
      saveUserToStorage(userData);
      setPermReady(true);
    }).catch(() => {
      // 刷新失败也要放行，避免永久卡在 loading
      setPermReady(true);
    });
  }, [permReady]);

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (!permReady) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="加载权限数据..." />
      </div>
    );
  }

  return children;
}

// 权限路由包装器
function PermissionRoute({ path, children }) {
  if (!canAccessRoute(path)) {
    return <ForbiddenPage />;
  }
  return children;
}

// 403 无权限页面
function ForbiddenPage() {
  return (
    <Result
      status="403"
      title="403"
      subTitle="抱歉，您没有权限访问此页面。请联系管理员获取权限。"
      extra={
        <Button type="primary" onClick={() => window.location.href = '/'}>
          返回首页
        </Button>
      }
    />
  );
}

// 主应用路由（在 EMLayout 内的路由）
function LayoutRoutes() {
  return (
    <EMLayout>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<PermissionRoute path="/"><Dashboard /></PermissionRoute>} />
          <Route path="/databases" element={<PermissionRoute path="/databases"><DatabaseList /></PermissionRoute>} />
          <Route path="/databases/:id" element={<PermissionRoute path="/databases/:id"><DatabaseDetail /></PermissionRoute>} />
          <Route path="/databases/:id/performance" element={<PermissionRoute path="/databases/:id/performance"><DatabasePerformanceHub /></PermissionRoute>} />
          <Route path="/alerts" element={<PermissionRoute path="/alerts"><AlertList /></PermissionRoute>} />
          <Route path="/alert-config" element={<PermissionRoute path="/alert-config"><AlertConfig /></PermissionRoute>} />
          <Route path="/capacity" element={<PermissionRoute path="/capacity"><CapacityPlanning /></PermissionRoute>} />
          <Route path="/tickets" element={<PermissionRoute path="/tickets"><TicketManagement /></PermissionRoute>} />
          <Route path="/sql-monitoring" element={<PermissionRoute path="/sql-monitoring"><SQLMonitoring /></PermissionRoute>} />
          <Route path="/notification-settings" element={<PermissionRoute path="/notification-settings"><NotificationSettings /></PermissionRoute>} />
          <Route path="/business-systems" element={<PermissionRoute path="/business-systems"><BusinessSystems /></PermissionRoute>} />
          <Route path="/reports" element={<PermissionRoute path="/reports"><ReportList /></PermissionRoute>} />
          <Route path="/user-management" element={<PermissionRoute path="/user-management"><UserManagement /></PermissionRoute>} />
          <Route path="/403" element={<ForbiddenPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ErrorBoundary>
    </EMLayout>
  );
}

function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1890ff',
          borderRadius: 4,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif",
        },
      }}
    >
      <AntApp>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/*"
            element={
              <PrivateRoute>
                <LayoutRoutes />
              </PrivateRoute>
            }
          />
        </Routes>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
