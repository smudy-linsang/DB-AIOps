import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Drawer, Segmented, Space, Switch, Tabs, Tag, Typography, message,
} from 'antd';
import { DiffOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { databaseAPI, incidentAPI } from '../services/api';
import ErrorBoundary from '../components/ErrorBoundary';
import HomeTab from '../components/perf/tabs/HomeTab';
import TopActivityTab from '../components/perf/tabs/TopActivityTab';
import AshTab from '../components/perf/tabs/AshTab';
import SqlTab from '../components/perf/tabs/SqlTab';
import BlockingTab from '../components/perf/tabs/BlockingTab';
import CompareDrawer from '../components/perf/CompareDrawer';
import SqlDetailPanel from '../components/perf/SqlDetailPanel';

const { Title, Text } = Typography;

const WINDOWS = [
  { label: '30分钟', value: '30m' }, { label: '1小时', value: '1h' },
  { label: '6小时', value: '6h' }, { label: '24小时', value: '24h' },
  { label: '7天', value: '7d' },
];

/**
 * Phase 7C: 性能中心 (对标 EMCC 性能大模块, phase7/30)。
 * 五 Tab: 性能主页 / 顶级活动 / ASH 分析 / SQL 监控 / 阻塞分析。
 * URL: /databases/:id/performance?tab=home|top|ash|sql|blocking&window=1h
 */
export default function PerformanceCenter() {
  const { id } = useParams();
  const configId = Number(id);
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') || 'home';
  const windowKey = sp.get('window') || '1h';

  const [db, setDb] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [compareOpen, setCompareOpen] = useState(false);
  const [sqlDrawer, setSqlDrawer] = useState(null);   // digest → 抽屉
  const [selectedDigest, setSelectedDigest] = useState(null); // Tab4 常驻详情
  const [openIncidents, setOpenIncidents] = useState([]);

  const range = useMemo(() => {
    const from = sp.get('from'), to = sp.get('to');
    return from && to ? { from, to } : { window: windowKey };
  }, [sp, windowKey]);

  useEffect(() => {
    databaseAPI.getDetail(configId).then((r) => setDb(r.data || r)).catch(() => {});
  }, [configId]);

  // 事故联动横幅 (phase7/30 §7)
  useEffect(() => {
    incidentAPI.list({ config_id: configId, status: 'open,diagnosing,plan_ready,executing,verifying' })
      .then((r) => setOpenIncidents((r.data?.items || r.items || []).slice(0, 3)))
      .catch(() => setOpenIncidents([]));
  }, [configId, refreshKey]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const t = setInterval(() => setRefreshKey((k) => k + 1), 10000);
    return () => clearInterval(t);
  }, [autoRefresh]);

  const setParam = useCallback((kv) => {
    const next = new URLSearchParams(sp);
    Object.entries(kv).forEach(([k, v]) => (v == null ? next.delete(k) : next.set(k, v)));
    setSp(next, { replace: true });
  }, [sp, setSp]);

  const openSql = useCallback((digest) => {
    setSelectedDigest(digest);
    if (tab === 'sql') return; // Tab4 常驻面板直接刷新
    setSqlDrawer(digest);
  }, [tab]);

  const common = { configId, range, refreshKey, onOpenSql: openSql };
  const items = [
    { key: 'home', label: '性能主页', children: <ErrorBoundary><HomeTab {...common} /></ErrorBoundary> },
    { key: 'top', label: '顶级活动', children: <ErrorBoundary><TopActivityTab {...common} /></ErrorBoundary> },
    { key: 'ash', label: 'ASH 分析',
      children: <ErrorBoundary><AshTab {...common} initFilters={parseFilters(sp.get('filters'))} /></ErrorBoundary> },
    { key: 'sql', label: 'SQL 监控',
      children: <ErrorBoundary><SqlTab {...common} selectedDigest={selectedDigest} /></ErrorBoundary> },
    { key: 'blocking', label: '阻塞分析', children: <ErrorBoundary><BlockingTab {...common} /></ErrorBoundary> },
  ];

  return (
    <div style={{ padding: 12 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }} wrap>
        <Space>
          <Button size="small" icon={<ArrowLeftOutlined />}
                  onClick={() => navigate(`/databases/${configId}`)}>实例详情</Button>
          <Title level={4} style={{ margin: 0 }}>性能中心</Title>
          {db && <Tag color="blue">{db.name} ({String(db.db_type || '').toUpperCase()})</Tag>}
        </Space>
        <Space>
          <Segmented size="small" options={WINDOWS} value={windowKey}
                      onChange={(v) => setParam({ window: v, from: null, to: null })} />
          <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh}
                  checkedChildren="10s" unCheckedChildren="自动" />
          <Button size="small" icon={<DiffOutlined />}
                  onClick={() => setCompareOpen(true)}>期间对比</Button>
        </Space>
      </Space>
      {openIncidents.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message={
            <Space wrap>
              <Text>该实例有 {openIncidents.length} 个进行中事故:</Text>
              {openIncidents.map((i) => (
                <Link key={i.incident_id} to={`/incidents/${i.incident_id}`}>
                  <Tag color={i.priority === 'P1' ? 'red' : 'orange'}>
                    {i.incident_id} {i.title}
                  </Tag>
                </Link>
              ))}
            </Space>
          } />
      )}
      <Tabs activeKey={tab} items={items} destroyInactiveTabPane
            onChange={(k) => setParam({ tab: k })} />
      <CompareDrawer configId={configId} open={compareOpen}
                     onClose={() => setCompareOpen(false)} onOpenSql={openSql} />
      <Drawer title={`SQL 详情: ${(sqlDrawer || '').slice(0, 16)}…`} width={920}
              open={!!sqlDrawer} onClose={() => setSqlDrawer(null)} destroyOnClose>
        {sqlDrawer && <SqlDetailPanel configId={configId} digest={sqlDrawer} />}
      </Drawer>
    </div>
  );
}

function parseFilters(s) {
  if (!s) return [];
  return s.split(',').filter((kv) => kv.includes(':'))
    .map((kv) => { const [col, ...rest] = kv.split(':'); return { col, value: rest.join(':') }; });
}
