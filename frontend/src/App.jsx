import React, { useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { ConfigProvider, App as AntApp } from 'antd';
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
import Login from './pages/Login';

const TOKEN_KEY = 'auth_token';

// 私密路由包装器
function PrivateRoute({ children }) {
  const token = localStorage.getItem(TOKEN_KEY);
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return children;
}

// 主应用路由（在 EMLayout 内的路由）
function LayoutRoutes() {
  return (
    <EMLayout>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/databases" element={<DatabaseList />} />
          <Route path="/databases/:id" element={<DatabaseDetail />} />
          <Route path="/databases/:id/performance" element={<DatabasePerformanceHub />} />
          <Route path="/alerts" element={<AlertList />} />
          <Route path="/alert-config" element={<AlertConfig />} />
          <Route path="/capacity" element={<CapacityPlanning />} />
          <Route path="/tickets" element={<TicketManagement />} />
          <Route path="/sql-monitoring" element={<SQLMonitoring />} />
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
