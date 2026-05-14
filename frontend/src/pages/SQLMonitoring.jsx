/**
 * SQLMonitoring - 仿 Oracle EMCC SQL Monitoring
 *
 * Phase 5b: 完整慢查询监控页面
 * 功能：
 *  - 数据库选择器 + 时间范围选择
 *  - 慢查询列表（SQL 文本、执行次数、耗时、扫描行数等）
 *  - SQL 文本搜索
 *  - 慢查询模式分析（全表扫描、缺失索引、高频小查询、大数据扫描、排序操作）
 *  - 优化建议生成
 *  - 行点击展开详情抽屉（概览/模式分析/优化建议三个 Tab）
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card, Table, Select, Input, Button, Drawer, Tabs, Tag, Space,
  Typography, Statistic, Row, Col, Spin, Empty, message, Tooltip, Badge,
  Descriptions, List, Alert, Progress,
} from 'antd';
import {
  ReloadOutlined, SearchOutlined, DatabaseOutlined,
  ClockCircleOutlined, ThunderboltOutlined, BugOutlined,
  BulbOutlined, WarningOutlined, InfoCircleOutlined,
  CloseCircleOutlined, CheckCircleOutlined,
  BarChartOutlined, FileSearchOutlined, SettingOutlined,
} from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { databaseAPI, sqlMonitoringAPI } from '../services/api';
import useAppStore from '../stores/useAppStore';

const { Title, Text, Paragraph } = Typography;

// ─── 时间范围选项 ───────────────────────────────────────────
const TIME_RANGE_OPTIONS = [
  { value: '1h', label: '最近 1 小时' },
  { value: '6h', label: '最近 6 小时' },
  { value: '24h', label: '最近 24 小时' },
];

// ─── 慢查询严重级别颜色 ─────────────────────────────────────
const SEVERITY_CONFIG = {
  critical: { color: '#f5222d', bg: '#fff1f0', label: '严重', icon: <CloseCircleOutlined /> },
  warning: { color: '#faad14', bg: '#fffbe6', label: '警告', icon: <WarningOutlined /> },
  info: { color: '#1890ff', bg: '#e6f7ff', label: '提示', icon: <InfoCircleOutlined /> },
};

// 根据平均耗时判断严重级别
function getSeverity(avgTimeSec) {
  if (avgTimeSec >= 10) return 'critical';
  if (avgTimeSec >= 1) return 'warning';
  return 'info';
}

// ─── 格式化辅助函数 ─────────────────────────────────────────
function formatMs(sec) {
  if (sec == null || sec === 0) return '0 ms';
  if (sec < 0.001) return `${(sec * 1000000).toFixed(0)} µs`;
  if (sec < 1) return `${(sec * 1000).toFixed(1)} ms`;
  if (sec < 60) return `${sec.toFixed(2)} s`;
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(0);
  return `${m}m ${s}s`;
}

function formatNumber(n) {
  if (n == null) return '-';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

function truncateSql(sql, maxLen = 80) {
  if (!sql) return '';
  return sql.length > maxLen ? sql.substring(0, maxLen) + '…' : sql;
}

// ─── 主组件 ─────────────────────────────────────────────────
const SQLMonitoring = () => {
  const [searchParams] = useSearchParams();
  const { databases, setDatabases } = useAppStore();

  // 状态
  const [dbId, setDbId] = useState(() => {
    const fromUrl = searchParams.get('db');
    return fromUrl ? Number(fromUrl) : null;
  });
  const [timeRange, setTimeRange] = useState('1h');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [slowQueries, setSlowQueries] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [dbType, setDbType] = useState('');
  const [analysis, setAnalysis] = useState(null);
  const [selectedQuery, setSelectedQuery] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState('overview');
  const [dbOptions, setDbOptions] = useState([]);

  // 加载数据库列表（用于选择器）
  useEffect(() => {
    if (databases.length > 0) {
      setDbOptions(databases.map((db) => ({
        value: db.id,
        label: `${db.name || db.host} (${(db.db_type || '').toUpperCase()})`,
        dbType: db.db_type,
      })));
      return;
    }
    databaseAPI.list().then((res) => {
      const dbs = (res.databases || res.data || []);
      setDatabases(dbs);
      setDbOptions(dbs.map((db) => ({
        value: db.id,
        label: `${db.name || db.host} (${(db.db_type || '').toUpperCase()})`,
        dbType: db.db_type,
      })));
    }).catch(() => {});
  }, []);

  // 加载慢查询列表
  const loadSlowQueries = useCallback(async () => {
    if (!dbId) return;
    setLoading(true);
    try {
      const params = { time: timeRange, limit: 100, sort_by: 'total_time' };
      const res = await sqlMonitoringAPI.list(dbId, params);
      const data = res.data || res;
      setSlowQueries(data.slow_queries || []);
      setTotalCount(data.total_count || 0);
      setDbType(data.db_type || '');
    } catch (e) {
      message.error('加载慢查询失败');
      setSlowQueries([]);
    } finally {
      setLoading(false);
    }
  }, [dbId, timeRange]);

  // 加载慢查询分析
  const loadAnalysis = useCallback(async () => {
    if (!dbId) return;
    setAnalysisLoading(true);
    try {
      const params = { time: timeRange };
      const res = await sqlMonitoringAPI.getAnalysis(dbId, params);
      const data = res.data || res;
      setAnalysis(data.analysis || null);
    } catch (e) {
      setAnalysis(null);
    } finally {
      setAnalysisLoading(false);
    }
  }, [dbId, timeRange]);

  // 搜索 SQL 文本
  const handleSearch = useCallback(async () => {
    if (!dbId || !searchKeyword.trim()) {
      loadSlowQueries();
      return;
    }
    setLoading(true);
    try {
      const params = { time: timeRange, q: searchKeyword.trim(), limit: 100 };
      const res = await sqlMonitoringAPI.search(dbId, params);
      const data = res.data || res;
      setSlowQueries(data.slow_queries || []);
      setTotalCount(data.total_count || 0);
    } catch (e) {
      message.error('搜索失败');
    } finally {
      setLoading(false);
    }
  }, [dbId, timeRange, searchKeyword, loadSlowQueries]);

  // DB 或时间变化时重新加载
  useEffect(() => {
    if (dbId) {
      loadSlowQueries();
      loadAnalysis();
    }
  }, [dbId, timeRange]);

  // 点击行查看详情
  const handleRowClick = (record) => {
    setSelectedQuery(record);
    setDrawerTab('overview');
    setDrawerOpen(true);
  };

  // 当前已选数据库信息
  const currentDb = dbOptions.find((d) => d.value === dbId);
  const currentDbType = currentDb?.dbType || dbType;

  // 统计摘要数据
  const summaryStats = useMemo(() => {
    if (!slowQueries.length) return null;
    const totalTime = slowQueries.reduce((s, q) => s + (q.total_time_sec || 0), 0);
    const totalExecs = slowQueries.reduce((s, q) => s + (q.exec_count || 0), 0);
    const avgTime = totalExecs > 0 ? totalTime / totalExecs : 0;
    const maxTime = Math.max(...slowQueries.map((q) => q.max_time_sec || q.avg_time_sec || 0));
    const noIndexCount = slowQueries.filter((q) => (q.no_index_used || 0) > 0).length;
    return { totalTime, totalExecs, avgTime, maxTime, noIndexCount, queryCount: slowQueries.length };
  }, [slowQueries]);

  // ─── 表格列定义 ───────────────────────────────────────────
  const columns = useMemo(() => {
    const baseCols = [
      {
        title: 'SQL 文本',
        dataIndex: 'query',
        key: 'query',
        width: 340,
        ellipsis: true,
        render: (text) => (
          <Tooltip title={text} placement="topLeft" overlayStyle={{ maxWidth: 600 }}>
            <Text code style={{ fontSize: 12, wordBreak: 'break-all' }}>
              {truncateSql(text, 100)}
            </Text>
          </Tooltip>
        ),
      },
      {
        title: '执行次数',
        dataIndex: 'exec_count',
        key: 'exec_count',
        width: 90,
        align: 'right',
        render: (v) => <Text>{formatNumber(v)}</Text>,
      },
      {
        title: '总耗时',
        dataIndex: 'total_time_sec',
        key: 'total_time_sec',
        width: 100,
        align: 'right',
        sorter: (a, b) => (a.total_time_sec || 0) - (b.total_time_sec || 0),
        defaultSortOrder: 'descend',
        render: (v) => <Text strong>{formatMs(v)}</Text>,
      },
      {
        title: '平均耗时',
        dataIndex: 'avg_time_sec',
        key: 'avg_time_sec',
        width: 100,
        align: 'right',
        sorter: (a, b) => (a.avg_time_sec || 0) - (b.avg_time_sec || 0),
        render: (v) => {
          const sev = getSeverity(v);
          const cfg = SEVERITY_CONFIG[sev];
          return <Tag color={cfg.color}>{formatMs(v)}</Tag>;
        },
      },
      {
        title: '最大耗时',
        dataIndex: 'max_time_sec',
        key: 'max_time_sec',
        width: 100,
        align: 'right',
        render: (v) => <Text>{formatMs(v)}</Text>,
      },
      {
        title: '扫描行数',
        dataIndex: 'rows_examined',
        key: 'rows_examined',
        width: 90,
        align: 'right',
        render: (v) => <Text>{formatNumber(v)}</Text>,
      },
      {
        title: '索引',
        key: 'index_status',
        width: 70,
        align: 'center',
        render: (_, record) => {
          const noIndex = record.no_index_used || 0;
          if (noIndex > 0) {
            return <Tag color="error" icon={<CloseCircleOutlined />}>未使用</Tag>;
          }
          if (record.no_good_index_used > 0) {
            return <Tag color="warning" icon={<WarningOutlined />}>不佳</Tag>;
          }
          return <Tag color="success" icon={<CheckCircleOutlined />}>正常</Tag>;
        },
      },
      {
        title: '严重度',
        key: 'severity',
        width: 80,
        align: 'center',
        render: (_, record) => {
          const sev = getSeverity(record.avg_time_sec);
          const cfg = SEVERITY_CONFIG[sev];
          return (
            <Badge
              status={sev === 'critical' ? 'error' : sev === 'warning' ? 'warning' : 'processing'}
              text={<Text style={{ fontSize: 12, color: cfg.color }}>{cfg.label}</Text>}
            />
          );
        },
      },
    ];

    // Oracle 额外列
    if (currentDbType === 'oracle') {
      baseCols.splice(6, 0, {
        title: 'SQL ID',
        dataIndex: 'sql_id',
        key: 'sql_id',
        width: 120,
        render: (v) => v ? <Text code style={{ fontSize: 11 }}>{v}</Text> : '-',
      });
    }

    return baseCols;
  }, [currentDbType]);

  // ─── 渲染 ─────────────────────────────────────────────────
  return (
    <div>
      {/* 标题栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <FileSearchOutlined style={{ fontSize: 20, color: '#1890ff' }} />
          <Title level={4} style={{ margin: 0 }}>SQL 慢查询监控</Title>
          {currentDbType && (
            <Tag color="blue">{currentDbType.toUpperCase()}</Tag>
          )}
        </Space>
        <Button
          icon={<ReloadOutlined spin={loading} />}
          onClick={() => { loadSlowQueries(); loadAnalysis(); }}
          loading={loading}
        >
          刷新
        </Button>
      </div>

      {/* 控制栏：数据库选择 + 时间范围 + 搜索 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap size={12}>
          <Space size={4}>
            <DatabaseOutlined />
            <Select
              placeholder="选择数据库"
              value={dbId}
              onChange={(val) => { setDbId(val); setSearchKeyword(''); }}
              options={dbOptions}
              style={{ width: 280 }}
              showSearch
              filterOption={(input, option) =>
                option.label.toLowerCase().includes(input.toLowerCase())
              }
              notFoundContent={<Empty description="暂无数据库" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            />
          </Space>

          <Space size={4}>
            <ClockCircleOutlined />
            <Select
              value={timeRange}
              onChange={setTimeRange}
              options={TIME_RANGE_OPTIONS}
              style={{ width: 150 }}
            />
          </Space>

          <Input.Search
            placeholder="搜索 SQL 文本…"
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            onSearch={handleSearch}
            style={{ width: 280 }}
            allowClear
            enterButton={<SearchOutlined />}
          />
        </Space>
      </Card>

      {/* 无数据库选择时的提示 */}
      {!dbId && (
        <Card>
          <Empty
            description={
              <span>
                请从上方选择数据库，或从左侧导航树右键菜单进入 SQL 监控
              </span>
            }
          />
        </Card>
      )}

      {/* 统计摘要卡片 */}
      {dbId && summaryStats && !loading && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="慢查询数"
                value={summaryStats.queryCount}
                suffix={`/ ${totalCount}`}
                valueStyle={{ color: summaryStats.queryCount > 20 ? '#f5222d' : '#1890ff', fontSize: 22 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="累计耗时"
                value={formatMs(summaryStats.totalTime)}
                valueStyle={{ fontSize: 18 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="平均耗时"
                value={formatMs(summaryStats.avgTime)}
                valueStyle={{ color: summaryStats.avgTime > 1 ? '#faad14' : '#52c41a', fontSize: 18 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="最大耗时"
                value={formatMs(summaryStats.maxTime)}
                valueStyle={{ color: '#f5222d', fontSize: 18 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="总执行次数"
                value={formatNumber(summaryStats.totalExecs)}
                valueStyle={{ fontSize: 18 }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title="未用索引"
                value={summaryStats.noIndexCount}
                suffix="条"
                valueStyle={{ color: summaryStats.noIndexCount > 0 ? '#f5222d' : '#52c41a', fontSize: 20 }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 分析摘要（模式数 + 建议数） */}
      {dbId && analysis && !analysisLoading && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
          <Col span={24}>
            <Card size="small">
              <Space size={24} wrap>
                <Space size={4}>
                  <BugOutlined style={{ color: '#faad14' }} />
                  <Text>检测到 </Text>
                  <Text strong style={{ fontSize: 16, color: '#faad14' }}>{analysis.patterns?.length || 0}</Text>
                  <Text> 个异常模式</Text>
                </Space>
                <Space size={4}>
                  <BulbOutlined style={{ color: '#1890ff' }} />
                  <Text>生成 </Text>
                  <Text strong style={{ fontSize: 16, color: '#1890ff' }}>{analysis.optimization_suggestions?.length || 0}</Text>
                  <Text> 条优化建议</Text>
                </Space>
                <Button
                  type="link"
                  size="small"
                  icon={<BarChartOutlined />}
                  onClick={() => {
                    setSelectedQuery(null);
                    setDrawerTab('patterns');
                    setDrawerOpen(true);
                  }}
                >
                  查看分析详情
                </Button>
              </Space>
            </Card>
          </Col>
        </Row>
      )}

      {/* 慢查询表格 */}
      {dbId && (
        <Card
          size="small"
          title={
            <Space>
              <ThunderboltOutlined style={{ color: '#faad14' }} />
              <Text strong>慢查询列表</Text>
              {!loading && (
                <Tag>{slowQueries.length} 条</Tag>
              )}
            </Space>
          }
        >
          <Spin spinning={loading}>
            {slowQueries.length === 0 && !loading ? (
              <Empty description="当前时间范围内无慢查询记录" />
            ) : (
              <Table
                columns={columns}
                dataSource={slowQueries.map((q, i) => ({ ...q, _key: i }))}
                rowKey="_key"
                size="small"
                scroll={{ x: 1000 }}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
                onRow={(record) => ({
                  onClick: () => handleRowClick(record),
                  style: { cursor: 'pointer' },
                })}
              />
            )}
          </Spin>
        </Card>
      )}

      {/* ─── 详情抽屉 ─────────────────────────────────────── */}
      <Drawer
        title={
          <Space>
            <FileSearchOutlined />
            <span>{selectedQuery ? 'SQL 详情' : '慢查询分析报告'}</span>
          </Space>
        }
        placement="right"
        width={720}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={
          <Tabs
            activeKey={drawerTab}
            onChange={setDrawerTab}
            size="small"
            items={[
              { key: 'overview', label: '概览' },
              { key: 'patterns', label: '模式分析' },
              { key: 'suggestions', label: '优化建议' },
            ]}
          />
        }
      >
        {/* Tab 1: 概览 */}
        {drawerTab === 'overview' && selectedQuery && (
          <div>
            <Title level={5}>SQL 文本</Title>
            <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
              <Paragraph
                code
                style={{
                  fontSize: 12,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  maxHeight: 200,
                  overflow: 'auto',
                  margin: 0,
                }}
              >
                {selectedQuery.query || '(无 SQL 文本)'}
              </Paragraph>
            </Card>

            <Title level={5}>执行统计</Title>
            <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="执行次数">
                <Text strong>{formatNumber(selectedQuery.exec_count)}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="总耗时">
                <Text strong style={{ color: '#f5222d' }}>{formatMs(selectedQuery.total_time_sec)}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="平均耗时">
                <Tag color={SEVERITY_CONFIG[getSeverity(selectedQuery.avg_time_sec)].color}>
                  {formatMs(selectedQuery.avg_time_sec)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="最大耗时">
                <Text>{formatMs(selectedQuery.max_time_sec)}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="扫描行数">
                <Text>{formatNumber(selectedQuery.rows_examined)}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="返回行数">
                <Text>{formatNumber(selectedQuery.rows_sent)}</Text>
              </Descriptions.Item>
              {selectedQuery.sort_rows != null && (
                <Descriptions.Item label="排序行数">
                  <Text>{formatNumber(selectedQuery.sort_rows)}</Text>
                </Descriptions.Item>
              )}
              {selectedQuery.sql_id && (
                <Descriptions.Item label="SQL ID">
                  <Text code>{selectedQuery.sql_id}</Text>
                </Descriptions.Item>
              )}
              {selectedQuery.first_seen && (
                <Descriptions.Item label="首次出现">
                  <Text>{selectedQuery.first_seen}</Text>
                </Descriptions.Item>
              )}
              {selectedQuery.last_seen && (
                <Descriptions.Item label="最近出现">
                  <Text>{selectedQuery.last_seen}</Text>
                </Descriptions.Item>
              )}
              {selectedQuery.buffer_gets_per_exec != null && (
                <Descriptions.Item label="Buffer Gets/次">
                  <Text>{formatNumber(selectedQuery.buffer_gets_per_exec)}</Text>
                </Descriptions.Item>
              )}
              {selectedQuery.disk_reads_per_exec != null && (
                <Descriptions.Item label="Disk Reads/次">
                  <Text>{formatNumber(selectedQuery.disk_reads_per_exec)}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>

            <Title level={5}>索引状态</Title>
            <Space size={8} style={{ marginBottom: 16 }}>
              {selectedQuery.no_index_used > 0 && (
                <Tag color="error" icon={<CloseCircleOutlined />}>
                  未使用索引 ({selectedQuery.no_index_used} 次)
                </Tag>
              )}
              {selectedQuery.no_good_index_used > 0 && (
                <Tag color="warning" icon={<WarningOutlined />}>
                  索引不佳 ({selectedQuery.no_good_index_used} 次)
                </Tag>
              )}
              {!selectedQuery.no_index_used && !selectedQuery.no_good_index_used && (
                <Tag color="success" icon={<CheckCircleOutlined />}>索引使用正常</Tag>
              )}
            </Space>
          </div>
        )}

        {/* Tab 2: 模式分析 */}
        {drawerTab === 'patterns' && (
          <div>
            {analysisLoading ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
            ) : !analysis || !analysis.patterns || analysis.patterns.length === 0 ? (
              <Empty description="暂无异常模式，系统运行正常" />
            ) : (
              <>
                <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
                  <Col span={8}>
                    <Card size="small">
                      <Statistic title="分析查询数" value={analysis.total_queries} suffix="条" />
                    </Card>
                  </Col>
                  <Col span={8}>
                    <Card size="small">
                      <Statistic title="累计耗时" value={formatMs(analysis.total_execution_time_sec)} />
                    </Card>
                  </Col>
                  <Col span={8}>
                    <Card size="small">
                      <Statistic title="总执行次数" value={formatNumber(analysis.total_executions)} />
                    </Card>
                  </Col>
                </Row>

                <Title level={5}>
                  <BugOutlined style={{ marginRight: 8, color: '#faad14' }} />
                  异常模式 ({analysis.patterns.length})
                </Title>

                <List
                  dataSource={analysis.patterns}
                  renderItem={(pattern) => {
                    const cfg = SEVERITY_CONFIG[pattern.severity] || SEVERITY_CONFIG.info;
                    return (
                      <Card
                        size="small"
                        style={{ marginBottom: 12 }}
                        title={
                          <Space>
                            <span style={{ color: cfg.color }}>{cfg.icon}</span>
                            <Text strong>{pattern.description}</Text>
                            <Tag color={cfg.color}>{cfg.label}</Tag>
                            <Tag>{pattern.count} 条查询</Tag>
                          </Space>
                        }
                      >
                        {pattern.examples && pattern.examples.length > 0 && (
                          <List
                            size="small"
                            dataSource={pattern.examples}
                            renderItem={(ex, idx) => (
                              <List.Item style={{ padding: '4px 0' }}>
                                <Text code style={{ fontSize: 11, wordBreak: 'break-all' }}>
                                  {truncateSql(ex.query, 120)}
                                </Text>
                                {ex.ratio != null && (
                                  <Tag style={{ marginLeft: 8 }}>
                                    扫描/返回: {ex.ratio.toFixed(0)}x
                                  </Tag>
                                )}
                                {ex.exec_count != null && (
                                  <Tag style={{ marginLeft: 4 }}>
                                    执行 {formatNumber(ex.exec_count)} 次
                                  </Tag>
                                )}
                              </List.Item>
                            )}
                          />
                        )}
                      </Card>
                    );
                  }}
                />
              </>
            )}
          </div>
        )}

        {/* Tab 3: 优化建议 */}
        {drawerTab === 'suggestions' && (
          <div>
            {analysisLoading ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
            ) : !analysis || !analysis.optimization_suggestions || analysis.optimization_suggestions.length === 0 ? (
              <Empty description="暂无优化建议" />
            ) : (
              <>
                <Title level={5}>
                  <BulbOutlined style={{ marginRight: 8, color: '#1890ff' }} />
                  优化建议 ({analysis.optimization_suggestions.length})
                </Title>

                <List
                  dataSource={analysis.optimization_suggestions}
                  renderItem={(suggestion, idx) => {
                    const priorityColor =
                      suggestion.priority === 'high' ? '#f5222d' :
                      suggestion.priority === 'medium' ? '#faad14' :
                      suggestion.priority === 'low' ? '#52c41a' : '#1890ff';
                    return (
                      <Card
                        size="small"
                        style={{ marginBottom: 12 }}
                        title={
                          <Space>
                            <Tag color={priorityColor}>
                              {suggestion.priority === 'high' ? '高优' :
                               suggestion.priority === 'medium' ? '中优' :
                               suggestion.priority === 'low' ? '低优' : '建议'}
                            </Tag>
                            <Text strong>{suggestion.suggestion}</Text>
                          </Space>
                        }
                      >
                        <Space direction="vertical" size={4}>
                          <Text type="secondary">
                            <SettingOutlined style={{ marginRight: 4 }} />
                            类别: {
                              suggestion.category === 'index_optimization' ? '索引优化' :
                              suggestion.category === 'sql_optimization' ? 'SQL 优化' :
                              suggestion.category === 'application_optimization' ? '应用层优化' :
                              suggestion.category
                            }
                          </Text>
                          <Alert
                            message="推荐操作"
                            description={suggestion.action}
                            type={
                              suggestion.priority === 'high' ? 'error' :
                              suggestion.priority === 'medium' ? 'warning' :
                              'info'
                            }
                            showIcon
                            style={{ marginTop: 8 }}
                          />
                        </Space>
                      </Card>
                    );
                  }}
                />
              </>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
};

export default SQLMonitoring;
