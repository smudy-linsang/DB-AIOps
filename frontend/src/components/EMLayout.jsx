/**
 * EMLayout - Oracle EMCC 风格核心布局
 *
 * 布局结构：
 * ┌──────────────────────────────────────────┐
 * │  顶部栏：Logo + 搜索 + 通知 + 用户       │
 * ├────────┬─────────────────────────────────┤
 * │  左侧  │        主内容区                  │
 * │  导航  │     (children)                  │
 * │  树    │                                 │
 * └────────┴─────────────────────────────────┘
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Layout, Input, Badge, Dropdown, Avatar, Space, Typography, Tag, Menu, Breadcrumb,
} from 'antd';
import {
  MenuFoldOutlined, MenuUnfoldOutlined, SearchOutlined,
  BellOutlined, UserOutlined, SettingOutlined, LogoutOutlined,
  DatabaseOutlined, DashboardOutlined, AlertOutlined,
  AppstoreOutlined, ThunderboltOutlined, ToolOutlined,
  BellFilled, ApartmentOutlined, FileTextOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { authAPI, alertAPI } from '../services/api';
import useAppStore from '../stores/useAppStore';
import TargetNavigationTree from './TargetNavigationTree';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

const EMLayout = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    collapsed, toggleCollapsed, selectedDbName, selectedDbType,
    alertCounts, setAlertCounts, globalSearchKeyword, setGlobalSearchKeyword,
    connectSSE, disconnectSSE,
  } = useAppStore();

  const [userName, setUserName] = useState('');

  useEffect(() => {
    const user = localStorage.getItem('user')
      ? JSON.parse(localStorage.getItem('user'))
      : null;
    setUserName(user?.username || 'Admin');
  }, []);

  // 加载告警统计
  const loadAlertCounts = useCallback(async () => {
    try {
      const res = await alertAPI.list({ page_size: 1 });
      const countRes = res?.data || res || {};
      setAlertCounts({
        warning: countRes.warning_count || 0,
        error: countRes.error_count || 0,
        critical: countRes.critical_count || 0,
      });
    } catch (_) {}
  }, [setAlertCounts]);

  useEffect(() => {
    loadAlertCounts();
    const timer = setInterval(loadAlertCounts, 60000);
    return () => clearInterval(timer);
  }, [loadAlertCounts]);

  // SSE 实时连接
  useEffect(() => {
    connectSSE();
    return () => disconnectSSE();
  }, []);

  const totalAlerts = alertCounts.warning + alertCounts.error + alertCounts.critical;

  // 根据路径获取面包屑
  const getBreadcrumbs = () => {
    const path = location.pathname;
    const crumbs = [{ title: <><DashboardOutlined /> 首页</>, path: '/' }];

    if (path.includes('/databases') && !path.includes('/databases/')) {
      crumbs.push({ title: '数据库管理', path: '/databases' });
    } else if (path.includes('/alerts')) {
      crumbs.push({ title: '告警中心', path: '/alerts' });
    } else if (path.includes('/alert-config')) {
      crumbs.push({ title: '告警配置', path: '/alert-config' });
    } else if (path.includes('/capacity')) {
      crumbs.push({ title: '容量规划', path: '/capacity' });
    } else if (path.includes('/tickets')) {
      crumbs.push({ title: '工单管理', path: '/tickets' });
    } else if (path.includes('/sql-monitoring')) {
      crumbs.push({ title: 'SQL 监控', path: '/sql-monitoring' });
    } else if (path.includes('/notification-settings')) {
      crumbs.push({ title: '通知设置', path: '/notification-settings' });
    } else if (path.includes('/business-systems')) {
      crumbs.push({ title: '业务拓扑', path: '/business-systems' });
    } else if (path.includes('/reports')) {
      crumbs.push({ title: '报表中心', path: '/reports' });
    }

    return crumbs;
  };

  const handleLogout = async () => {
    try {
      await authAPI.logout();
    } catch (_) {}
    localStorage.removeItem('auth_token');
    localStorage.removeItem('user');
    navigate('/login');
  };

  const userMenuItems = [
    { key: 'profile', label: '个人中心', icon: <UserOutlined /> },
    { key: 'settings', label: '系统设置', icon: <SettingOutlined /> },
    { type: 'divider' },
    { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, danger: true, onClick: handleLogout },
  ];

  const dbTypeColors = {
    oracle: '#f5222d', mysql: '#1890ff', pgsql: '#336791',
    dm: '#ee2222', gbase: '#00a854', tdsql: '#108ee9',
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* ──────── 顶部栏 ──────── */}
      <Header
        style={{
          background: '#1a3a5c',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 20px',
          height: 52,
          lineHeight: '52px',
          borderBottom: '3px solid #1890ff',
          zIndex: 100,
        }}
      >
        <Space size={16}>
          <div
            style={{
              fontSize: 18,
              fontWeight: 700,
              color: '#fff',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
            onClick={() => navigate('/')}
          >
            <DatabaseOutlined style={{ fontSize: 22, color: '#1890ff' }} />
            DB-AIOps
          </div>
          <Input
            prefix={<SearchOutlined style={{ color: '#999' }} />}
            placeholder="搜索数据库名称、主机、指标..."
            value={globalSearchKeyword}
            onChange={(e) => setGlobalSearchKeyword(e.target.value)}
            style={{
              width: 320,
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.2)',
              background: 'rgba(255,255,255,0.1)',
              color: '#fff',
            }}
            allowClear
          />
        </Space>

        <Space size={20}>
          <Badge count={totalAlerts} size="small" offset={[-2, 2]}>
            <BellOutlined
              style={{ fontSize: 18, color: totalAlerts > 0 ? '#faad14' : 'rgba(255,255,255,0.7)', cursor: 'pointer' }}
              onClick={() => navigate('/alerts')}
            />
          </Badge>
          <Dropdown menu={{ items: userMenuItems }} trigger={['click']}>
            <Space style={{ cursor: 'pointer', color: '#fff' }}>
              <Avatar size="small" icon={<UserOutlined />} style={{ background: '#1890ff' }} />
              <Text style={{ color: '#fff', fontSize: 13 }}>{userName}</Text>
            </Space>
          </Dropdown>
        </Space>
      </Header>

      <Layout>
        {/* ──────── 左侧导航树 ──────── */}
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={toggleCollapsed}
          trigger={null}
          width={260}
          collapsedWidth={0}
          style={{
            background: '#f5f7fa',
            borderRight: '1px solid #e8e8e8',
            overflow: 'auto',
          }}
        >
          <div style={{ padding: '8px 12px', borderBottom: '1px solid #e8e8e8' }}>
            <Space>
              <MenuUnfoldOutlined
                onClick={toggleCollapsed}
                style={{ fontSize: 16, cursor: 'pointer', color: '#666' }}
              />
              <Text strong style={{ fontSize: 13 }}>目标导航</Text>
            </Space>
          </div>
          <TargetNavigationTree />
          {/* 功能快捷菜单 */}
          <div style={{ borderTop: '1px solid #e8e8e8', padding: '8px 12px', marginTop: 4 }}>
            <Text strong style={{ fontSize: 12, color: '#666' }}>功能菜单</Text>
          </div>
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            style={{ border: 'none', background: 'transparent', fontSize: 12 }}
            items={[
              { key: '/', icon: <DashboardOutlined />, label: '仪表盘', onClick: () => navigate('/') },
              { key: '/databases', icon: <DatabaseOutlined />, label: '数据库管理', onClick: () => navigate('/databases') },
              { key: '/alerts', icon: <AlertOutlined />, label: '告警中心', onClick: () => navigate('/alerts') },
              { key: '/alert-config', icon: <ToolOutlined />, label: '告警配置', onClick: () => navigate('/alert-config') },
              { key: '/sql-monitoring', icon: <SearchOutlined />, label: 'SQL 监控', onClick: () => navigate('/sql-monitoring') },
              { key: '/capacity', icon: <ThunderboltOutlined />, label: '容量规划', onClick: () => navigate('/capacity') },
              { key: '/tickets', icon: <AppstoreOutlined />, label: '工单管理', onClick: () => navigate('/tickets') },
              { type: 'divider' },
              { key: '/notification-settings', icon: <BellFilled />, label: '通知设置', onClick: () => navigate('/notification-settings') },
              { key: '/business-systems', icon: <ApartmentOutlined />, label: '业务拓扑', onClick: () => navigate('/business-systems') },
              { key: '/reports', icon: <FileTextOutlined />, label: '报表中心', onClick: () => navigate('/reports') },
            ]}
          />
        </Sider>

        {/* ──────── 主内容区 ──────── */}
        <Content style={{ background: '#f0f2f5', minHeight: 'calc(100vh - 52px)' }}>
          {/* 折叠按钮 (Sider 折叠时显示) */}
          {collapsed && (
            <div
              style={{
                position: 'absolute',
                top: 60,
                left: 8,
                zIndex: 10,
                cursor: 'pointer',
                background: '#fff',
                padding: '6px 10px',
                borderRadius: 4,
                boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
              }}
              onClick={toggleCollapsed}
            >
              <MenuFoldOutlined style={{ fontSize: 16, color: '#666' }} />
            </div>
          )}

          {/* 面包屑 + 当前选中 DB 标识 */}
          <div style={{ padding: '8px 24px 0', background: '#f0f2f5' }}>
            <Space>
              <Breadcrumb
                items={getBreadcrumbs().map((c) => ({ title: c.path ? <a onClick={() => navigate(c.path)}>{c.title}</a> : c.title }))}
              />
              {selectedDbName && (
                <Tag color={dbTypeColors[selectedDbType] || '#1890ff'}>
                  <DatabaseOutlined /> {selectedDbName}
                </Tag>
              )}
            </Space>
          </div>

          {/* 页面内容 */}
          <div style={{ padding: '12px 24px 24px' }}>
            {children}
          </div>
        </Content>
      </Layout>
    </Layout>
  );
};

export default EMLayout;
