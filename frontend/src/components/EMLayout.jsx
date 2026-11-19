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
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Layout, Input, AutoComplete, Button, Empty, Badge, Dropdown, Avatar, Space, Typography, Tag, Menu, Breadcrumb, Alert,
} from 'antd';
import {
  MenuFoldOutlined, MenuUnfoldOutlined, SearchOutlined,
  BellOutlined, UserOutlined, SettingOutlined, LogoutOutlined,
  DatabaseOutlined, DashboardOutlined, AlertOutlined,
  AppstoreOutlined, ThunderboltOutlined, ToolOutlined,
  BellFilled, ApartmentOutlined, FileTextOutlined,
  TeamOutlined, CheckCircleOutlined, RobotOutlined, FireOutlined, KeyOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { authAPI, alertAPI, systemAPI, setUser } from '../services/api';
import useAppStore from '../stores/useAppStore';
import TargetNavigationTree from './TargetNavigationTree';
import { readStoredUser } from '../utils/experience';
import UsageGuide from './UsageGuide';
import CopilotDrawer from './CopilotDrawer';
import { hasPermission } from '../utils/permission';
import { Perm } from '../utils/permission';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

const EMLayout = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    collapsed, toggleCollapsed, setCollapsed, selectedDbId, selectedDbName, selectedDbType,
    alertCounts, setAlertCounts, globalSearchKeyword, setGlobalSearchKeyword,
    connectSSE, disconnectSSE, sseUnavailableReason,
  } = useAppStore();

  const [guideVisible, setGuideVisible] = useState(false);
  const databases = useAppStore(state => state.databases);
  const [copilotVisible, setCopilotVisible] = useState(false);
  const [userName, setUserName] = useState('');
  const [userRole, setUserRole] = useState('');
  const [permVersion, setPermVersion] = useState(0); // 权限版本号，用于触发 useMemo 重算

  useEffect(() => {
    const user = readStoredUser();
    setUserName(user?.username || 'Admin');
    setUserRole(user?.role_name || user?.role || '');

    // 检查 user 数据是否包含权限信息（兼容旧版本 localStorage 数据）
    if (user && (!user.permissions || !user.role)) {
      authAPI.getCurrentUser().then(res => {
        const userData = res.data || res;
        setUser(userData);
        setUserRole(userData.role_name || userData.role || '');
        setPermVersion(v => v + 1); // 触发菜单重新计算
      }).catch(() => {});
    }
  }, []);

  // 加载告警统计
  const loadAlertCounts = useCallback(async () => {
    try {
      const res = await alertAPI.getStatistics();
      const countRes = res?.ongoing_by_severity || {};
      setAlertCounts({
        warning: countRes.warning || 0,
        error: countRes.error || 0,
        critical: countRes.critical || 0,
      });
    } catch (_) {}
  }, [setAlertCounts]);

  // W4 自监控：系统健康指示灯数据（与告警统计共用下方 60s 定时器，不新增定时器）
  const [sysHealth, setSysHealth] = useState(null);
  const loadSystemHealth = useCallback(async () => {
    if (!hasPermission(Perm.DASHBOARD_VIEW)) return;
    try {
      const res = await systemAPI.health();
      setSysHealth(res?.data || res || null);
    } catch (_) { setSysHealth(null); }
  }, []);

  useEffect(() => {
    loadAlertCounts();
    loadSystemHealth();
    const timer = setInterval(() => {
      loadAlertCounts();
      loadSystemHealth();
    }, 60000);
    return () => clearInterval(timer);
  }, [loadAlertCounts, loadSystemHealth]);

  // SSE 实时连接
  useEffect(() => {
    connectSSE();
    return () => disconnectSSE();
  }, [connectSSE, disconnectSSE]);

  const totalAlerts = alertCounts.warning + alertCounts.error + alertCounts.critical;
  const systemStatus = sseUnavailableReason && sysHealth?.status !== 'down' ? 'degraded' : sysHealth?.status;

  // 根据路径获取面包屑
  const getBreadcrumbs = () => {
    const path = location.pathname;
    const crumbs = [{ title: <><DashboardOutlined /> 首页</>, path: '/' }];

    if (path.includes('/databases') && !path.includes('/databases/')) {
      crumbs.push({ title: '数据库管理', path: '/databases' });
    } else if (path === '/work-queue') {
      crumbs.push({ title: '值班队列' });
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
    } else if (path.includes('/inspection')) {
      crumbs.push({ title: '智能巡检', path: '/inspection' });
    } else if (path.startsWith('/oam')) {
      // V3.0 OAM：轨迹页（/oam/trajectories/:trajId）没有菜单入口，
      // 由诊断页的 traj_id 链接带进来，所以面包屑要给它一个可点回的一级页
      crumbs.push({ title: 'OAM 智能体', path: '/oam/diagnose' });
      const oamCrumbs = [
        ['/oam/diagnose', '智能诊断'],
        ['/oam/trajectories', '轨迹与证据审计'],
        ['/oam/bench', '评测成绩单'],
        ['/oam/kb', '知识库新鲜度'],
      ];
      const hit = oamCrumbs.find(([p]) => path.startsWith(p));
      if (hit && hit[0] !== '/oam/diagnose') {
        crumbs.push({ title: hit[1], path: hit[0] });
      }
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
    { key: 'guide', label: '使用指南', onClick: () => setGuideVisible(true) },
    { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, danger: true, onClick: handleLogout },
  ];

  const dbTypeColors = {
    oracle: '#f5222d', mysql: '#1890ff', pgsql: '#336791',
    dm: '#ee2222', gbase: '#00a854', tdsql: '#108ee9',
  };

  // 根据权限过滤菜单项
  const menuItems = useMemo(() => {
    const allItems = [
      { key: '/', icon: <DashboardOutlined />, label: '仪表盘', perm: Perm.DASHBOARD_VIEW },
      { key: '/databases', icon: <DatabaseOutlined />, label: '数据库管理', perm: Perm.DATABASES_VIEW },
      { key: '/work-queue', icon: <ThunderboltOutlined />, label: '值班队列', perm: Perm.ALERTS_VIEW },
      { key: '/alerts', icon: <AlertOutlined />, label: '告警中心', perm: Perm.ALERTS_VIEW },
      { key: '/alert-config', icon: <ToolOutlined />, label: '模板配置中心', perm: Perm.ALERT_CONFIG_VIEW },
      { key: '/sql-monitoring', icon: <SearchOutlined />, label: 'SQL 监控', perm: Perm.SQL_MONITORING_VIEW },
      { key: '/capacity', icon: <ThunderboltOutlined />, label: '容量规划', perm: Perm.CAPACITY_VIEW },
      { key: '/tickets', icon: <AppstoreOutlined />, label: '工单管理', perm: Perm.TICKETS_VIEW },
      { key: '/incidents', icon: <ThunderboltOutlined />, label: '事故中心', perm: Perm.ALERTS_VIEW },
      { key: '/incident-warroom', icon: <FireOutlined style={{ color: '#ef4444' }} />, label: '排障作战室', perm: Perm.ALERTS_VIEW },
      { key: '/sla-report', icon: <DashboardOutlined />, label: 'SLA 报表', perm: Perm.ALERTS_VIEW },
      { key: '/ai-ops', icon: <RobotOutlined />, label: 'AI 运营中心', perm: Perm.METRICS_VIEW },
      { key: '/llm-config', icon: <KeyOutlined style={{ color: '#8b5cf6' }} />, label: '大模型配置', perm: Perm.ALERT_CONFIG_VIEW },
      { key: '/inspection', icon: <CheckCircleOutlined />, label: '智能巡检', perm: Perm.DATABASES_VIEW },
      { key: '/oam/diagnose', icon: <RobotOutlined />, label: 'OAM 智能诊断', perm: Perm.TICKETS_EXECUTE },
      { key: '/oam/bench', icon: <DashboardOutlined />, label: 'OAM 评测成绩单', perm: Perm.METRICS_VIEW },
      { key: '/oam/kb', icon: <FileTextOutlined />, label: 'OAM 知识库', perm: Perm.ALERT_CONFIG_VIEW },
      { type: 'divider' },
      { key: '/notification-settings', icon: <BellFilled />, label: '通知设置', perm: Perm.NOTIFICATION_VIEW },
      { key: '/business-systems', icon: <ApartmentOutlined />, label: '业务拓扑', perm: Perm.BUSINESS_TOPOLOGY_VIEW },
      { key: '/reports', icon: <FileTextOutlined />, label: '报表中心', perm: Perm.REPORTS_VIEW },
      { key: '/user-management', icon: <TeamOutlined />, label: '用户管理', perm: Perm.USERS_VIEW },
    ];

    return allItems.filter(item => {
      if (item.type === 'divider') return true;
      return hasPermission(item.perm);
    }).map(item => {
      if (item.type === 'divider') return item;
      const { perm, ...rest } = item;
      return {
        ...rest,
        onClick: () => navigate(item.key),
      };
    });  }, [navigate, permVersion]);

  const searchOptions = useMemo(() => {
    const keyword = globalSearchKeyword.trim().toLowerCase();
    if (!keyword) return [];
    const functions = menuItems.filter(item => item.key && `${item.label} ${item.key}`.toLowerCase().includes(keyword))
      .map(item => ({ value: item.key, label: `功能 · ${item.label}` }));
    const targets = hasPermission(Perm.DATABASE_DETAIL_VIEW) ? databases
      .filter(db => `${db.name} ${db.host}`.toLowerCase().includes(keyword))
      .map(db => ({ value: `/databases/${db.id}`, label: `实例 · ${db.name}` })) : [];
    return [...functions, ...targets].slice(0, 20);
  }, [globalSearchKeyword, menuItems, databases]);
  const selectSearch = value => { setGlobalSearchKeyword(''); navigate(value); };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* ──────── 顶部栏 ──────── */}
      <Header className="experience-header"
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
          <Button ghost aria-label={collapsed ? '展开导航' : '折叠导航'} icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={toggleCollapsed} />
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
          <AutoComplete id="global-search" className="experience-search" value={globalSearchKeyword} options={searchOptions}
            onChange={setGlobalSearchKeyword} onSelect={selectSearch}
            notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未找到入口。支持功能名称、已加载实例的名称或主机；仅显示有权限的结果。" />}>
            <Input prefix={<SearchOutlined />} placeholder="搜索功能或实例…" aria-label="搜索功能或实例" allowClear
              onPressEnter={() => { if (searchOptions[0]) selectSearch(searchOptions[0].value); }} />
          </AutoComplete>
        </Space>

        <Space size={12} wrap>
          <Button ghost onClick={() => setGuideVisible(true)}>使用指南</Button>
          {hasPermission(Perm.DASHBOARD_VIEW) && (
            <Button ghost size="small" aria-label="查看系统健康" onClick={() => navigate('/system/health')}>
              {({ ok: '系统正常', degraded: '系统降级', down: '系统不可用' })[systemStatus] || '系统健康'}
            </Button>
          )}

          {/* Copilot 悬浮/顶部入口 */}
          <span
            onClick={() => setCopilotVisible(true)}
            style={{
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              background: 'linear-gradient(135deg, #1890ff 0%, #722ed1 100%)',
              padding: '2px 10px',
              borderRadius: 16,
              color: '#fff',
              fontSize: 12,
              fontWeight: 500,
              boxShadow: '0 2px 6px rgba(114, 46, 209, 0.4)',
              transition: 'all 0.3s'
            }}
          >
            <RobotOutlined style={{ fontSize: 14 }} />
            <span>AI Copilot</span>
          </span>

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
              {userRole && <Tag color="blue" style={{ marginLeft: 4, fontSize: 11 }}>{userRole}</Tag>}
            </Space>
          </Dropdown>
        </Space>
      </Header>

      <Layout>
        {/* ──────── 左侧导航树 ──────── */}
        <Sider
          collapsible
          collapsed={collapsed}
          breakpoint="md"
          onBreakpoint={broken => setCollapsed(broken)}
          onCollapse={value => setCollapsed(value)}
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
              <Text strong style={{ fontSize: 13 }}>目标导航</Text>
            </Space>
          </div>
          <TargetNavigationTree />
          {/* 功能快捷菜单 - 根据权限过滤 */}
          <div style={{ borderTop: '1px solid #e8e8e8', padding: '8px 12px', marginTop: 4 }}>
            <Text strong style={{ fontSize: 12, color: '#666' }}>功能菜单</Text>
          </div>
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            style={{ border: 'none', background: 'transparent', fontSize: 12 }}
            items={menuItems}
          />
        </Sider>

        {/* ──────── 主内容区 ──────── */}
        <Content id="main-content" style={{ minWidth: 0, background: '#f0f2f5', minHeight: 'calc(100vh - 52px)' }}>
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
          <div className="experience-content" style={{ padding: '12px 24px 24px' }}>
            {sseUnavailableReason && (
              <Alert type="warning" showIcon message="实时推送不可用"
                description={<>{sseUnavailableReason}<br />系统仍会每分钟尝试刷新顶部告警计数。</>}
                style={{ marginBottom: 12 }} />
            )}
            {children}
          </div>
        </Content>
      </Layout>

      <UsageGuide open={guideVisible} onClose={() => setGuideVisible(false)} />
      {/* 全局 AI Copilot 抽屉 */}
      <CopilotDrawer
        visible={copilotVisible}
        onClose={() => setCopilotVisible(false)}
        initialDbId={selectedDbId}
      />
    </Layout>
  );
};

export default EMLayout;
