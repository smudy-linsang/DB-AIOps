import React, { useEffect, useMemo, useState } from 'react';
import {
  Card, Col, Collapse, Empty, Progress, Row, Select, Space, Table, Tag,
  Typography, message,
} from 'antd';
import { CloseOutlined, FilterOutlined } from '@ant-design/icons';
import * as echarts from 'echarts/core';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { perfAPI } from '../../../services/api';
import WaitClassTag from '../WaitClassTag';

const { Text } = Typography;

const DIM_LABELS = {
  wait_class: '等待类', user_name: '用户', db_name: '库', sql_digest: 'SQL',
  wait_event: '等待事件', module: '模块', program: '程序',
  client_host: '客户端', lock_object: '争用对象',
};
const ALL_DIMS = Object.keys(DIM_LABELS);

/** Tab3 ASH 分析 (phase7/30 §4): 过滤器叠加 + 九维分布 + 明细 */
export default function AshTab({ configId, range, onOpenSql, refreshKey, initFilters }) {
  const [filters, setFilters] = useState(initFilters || []); // [{col,value}]
  const [data, setData] = useState(null);

  useEffect(() => {
    if (!configId) return;
    const params = {
      ...range, dims: ALL_DIMS.join(','),
      filters: filters.map((f) => `${f.col}:${f.value}`).join(','),
      limit: 100,
    };
    perfAPI.ashFacets(configId, params)
      .then((r) => setData(r.data))
      .catch((e) => message.error(`ASH 分面失败: ${e.message}`));
  }, [configId, range, filters, refreshKey]);

  const addFilter = (col, value) => {
    setFilters((fs) => [...fs.filter((f) => f.col !== col), { col, value }]);
  };
  const removeFilter = (col) => setFilters((fs) => fs.filter((f) => f.col !== col));

  const timelineOption = useMemo(() => ({
    grid: { left: 44, right: 12, top: 8, bottom: 20 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: 'AAS', axisLabel: { fontSize: 10 } },
    series: [{ type: 'line', symbol: 'none', areaStyle: { opacity: 0.3 },
               color: '#1868C8', data: (data?.timeline || []).map((p) => [p.t, p.aas]) }],
  }), [data]);

  const facetItems = ALL_DIMS.map((d) => ({
    key: d,
    label: `${DIM_LABELS[d]} (${(data?.facets?.[d] || []).length})`,
    children: (
      <div>
        {(data?.facets?.[d] || []).map((f) => (
          <div key={f.value} onClick={() => f.value !== '(null)' && addFilter(d, f.value)}
               style={{ cursor: 'pointer', marginBottom: 4 }}>
            <Space size={4} style={{ width: '100%', justifyContent: 'space-between' }}>
              <Text style={{ fontSize: 11, maxWidth: 130 }} ellipsis>{f.value}</Text>
              <Text type="secondary" style={{ fontSize: 11 }}>{f.pct}%</Text>
            </Space>
            <Progress percent={f.pct} showInfo={false} size="small" strokeColor="#1868C8" />
          </div>
        ))}
        {!(data?.facets?.[d] || []).length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      </div>
    ),
  }));

  return (
    <div>
      <Card size="small" styles={{ body: { padding: 8 } }}>
        <Space wrap>
          <FilterOutlined />
          {filters.length === 0 && <Text type="secondary">点击左侧维度条目添加过滤器 (同列互斥)</Text>}
          {filters.map((f) => (
            <Tag key={f.col} closable closeIcon={<CloseOutlined />}
                 onClose={() => removeFilter(f.col)} color="blue">
              {DIM_LABELS[f.col]} = {f.value}
            </Tag>
          ))}
          <Text type="secondary">命中活动 {data?.matched_active_sec ?? 0}s</Text>
        </Space>
      </Card>
      <Card size="small" style={{ marginTop: 8 }} styles={{ body: { padding: 4 } }}>
        <ReactEChartsCore echarts={echarts} option={timelineOption} notMerge style={{ height: 120 }} />
      </Card>
      <Row gutter={8} style={{ marginTop: 8 }}>
        <Col span={6}>
          <Card size="small" title="维度分布 (点击=加过滤)" styles={{ body: { padding: 8, maxHeight: 560, overflow: 'auto' } }}>
            <Collapse size="small" ghost items={facetItems}
                      defaultActiveKey={['wait_class', 'sql_digest']} />
          </Card>
        </Col>
        <Col span={18}>
          <Card size="small" title="命中样本明细">
            <Table size="small" rowKey={(r, i) => i} dataSource={data?.rows || []}
              pagination={{ pageSize: 15, size: 'small' }}
              columns={[
                { title: '时间', dataIndex: 'time', width: 150,
                  render: (v) => String(v).slice(11, 19) },
                { title: '会话', dataIndex: 'session_id', width: 80,
                  render: (v) => <Text code>{v}</Text> },
                { title: '用户', dataIndex: 'user_name', width: 90, ellipsis: true },
                { title: '等待类', dataIndex: 'wait_class', width: 90,
                  render: (v) => <WaitClassTag value={v} /> },
                { title: '等待事件', dataIndex: 'wait_event', width: 150, ellipsis: true },
                { title: 'SQL', dataIndex: 'sql_text', ellipsis: true,
                  render: (v, r) => (
                    <a onClick={() => r.sql_digest && onOpenSql?.(r.sql_digest)}>
                      <Text code style={{ fontSize: 11 }}>{v || r.sql_digest || '-'}</Text>
                    </a>) },
                { title: '争用对象', dataIndex: 'lock_object', width: 130, ellipsis: true },
                { title: '活跃秒', dataIndex: 'active_secs', width: 70 },
              ]} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
