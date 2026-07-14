import React, { useMemo, useState } from 'react';
import {
  Button, Col, DatePicker, Drawer, Radio, Row, Space, Table, Tag, Typography, message,
} from 'antd';
import dayjs from 'dayjs';
import * as echarts from 'echarts/core';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { perfAPI } from '../../services/api';
import { WAIT_CLASS_ORDER, waitClassColor, waitClassLabel } from './waitClassMeta';

const { Text } = Typography;
const { RangePicker } = DatePicker;

const PRESETS = {
  today_yesterday: () => {
    const now = dayjs();
    return {
      a: [now.subtract(1, 'day').startOf('day'), now.subtract(1, 'day')],
      b: [now.startOf('day'), now],
    };
  },
  hour_prev: () => {
    const now = dayjs();
    return { a: [now.subtract(2, 'hour'), now.subtract(1, 'hour')],
             b: [now.subtract(1, 'hour'), now] };
  },
};

/** 期间对比抽屉 (phase7/30 §7): AAS 按类双柱 + Top SQL diff */
export default function CompareDrawer({ configId, open, onClose, onOpenSql }) {
  const [preset, setPreset] = useState('hour_prev');
  const [rangeA, setRangeA] = useState(null);
  const [rangeB, setRangeB] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    let a = rangeA, b = rangeB;
    if (preset !== 'custom') {
      const p = PRESETS[preset]();
      a = p.a; b = p.b;
    }
    if (!a || !b) { message.warning('请选择两个时间窗'); return; }
    setLoading(true);
    try {
      const r = await perfAPI.compare(configId, {
        a_from: a[0].toISOString(), a_to: a[1].toISOString(),
        b_from: b[0].toISOString(), b_to: b[1].toISOString(),
      });
      setData(r.data);
    } catch (e) {
      message.error(`对比失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const barOption = useMemo(() => {
    if (!data) return {};
    const classes = WAIT_CLASS_ORDER.filter(
      (k) => (data.a.aas_by_class[k] || 0) + (data.b.aas_by_class[k] || 0) > 0);
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['窗口A', '窗口B'], top: 0 },
      grid: { left: 60, right: 16, top: 28, bottom: 40 },
      xAxis: { type: 'category', data: classes.map(waitClassLabel),
               axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', name: '活动秒' },
      series: [
        { name: '窗口A', type: 'bar', data: classes.map((k) => data.a.aas_by_class[k] || 0),
          itemStyle: { color: '#8C9BAB' } },
        { name: '窗口B', type: 'bar',
          data: classes.map((k) => ({ value: data.b.aas_by_class[k] || 0,
                                      itemStyle: { color: waitClassColor(k) } })) },
      ],
    };
  }, [data]);

  return (
    <Drawer title="期间对比" width={860} open={open} onClose={onClose}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Radio.Group size="small" value={preset} onChange={(e) => setPreset(e.target.value)}
          optionType="button" options={[
            { value: 'hour_prev', label: '近1h vs 前1h' },
            { value: 'today_yesterday', label: '今天 vs 昨天' },
            { value: 'custom', label: '自定义' },
          ]} />
        {preset === 'custom' && (
          <>
            <RangePicker showTime size="small" placeholder={['A 起', 'A 止']}
                         value={rangeA} onChange={setRangeA} />
            <RangePicker showTime size="small" placeholder={['B 起', 'B 止']}
                         value={rangeB} onChange={setRangeB} />
          </>
        )}
        <Button type="primary" size="small" loading={loading} onClick={run}>对比</Button>
        {data && (
          <Text type="secondary">
            A: DB Time {data.a.totals.db_time_sec}s / AAS {data.a.totals.avg_aas} →
            B: {data.b.totals.db_time_sec}s / {data.b.totals.avg_aas}
          </Text>
        )}
      </Space>
      {data && (
        <>
          <ReactEChartsCore echarts={echarts} option={barOption} notMerge style={{ height: 240 }} />
          <Table size="small" rowKey="key" style={{ marginTop: 8 }}
            title={() => 'Top SQL 差异 (按劣化倍率降序)'}
            dataSource={data.diff.top_sql} pagination={{ pageSize: 8, size: 'small' }}
            onRow={(r) => ({ onClick: () => r.key !== '(null)' && onOpenSql?.(r.key),
                             style: { cursor: 'pointer' } })}
            columns={[
              { title: 'SQL 指纹', dataIndex: 'key', ellipsis: true,
                render: (v) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
              { title: 'A 活动秒', dataIndex: 'a_active_sec', width: 90 },
              { title: 'B 活动秒', dataIndex: 'b_active_sec', width: 90 },
              { title: '倍率', dataIndex: 'ratio', width: 80,
                render: (v) => (v == null ? '-' : <Text strong type={v >= 2 ? 'danger' : undefined}>{v}x</Text>) },
              { title: '', width: 90,
                render: (_, r) => (
                  <Space size={4}>
                    {r.new && <Tag color="red">新增</Tag>}
                    {r.gone && <Tag>消失</Tag>}
                  </Space>) },
            ]} />
          <Row gutter={8} style={{ marginTop: 8 }}>
            {['a', 'b'].map((w) => (
              <Col span={12} key={w}>
                <Table size="small" rowKey="key" pagination={false}
                  title={() => `窗口${w.toUpperCase()} Top 等待事件`}
                  dataSource={data[w].top_wait_events}
                  columns={[
                    { title: '等待事件', dataIndex: 'key', ellipsis: true },
                    { title: '活动秒', dataIndex: 'active_sec', width: 90 },
                  ]} />
              </Col>
            ))}
          </Row>
        </>
      )}
    </Drawer>
  );
}
