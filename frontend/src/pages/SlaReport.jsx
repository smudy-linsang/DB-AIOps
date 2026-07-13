import React, { useState, useEffect, useCallback } from 'react'
import { Card, Row, Col, Statistic, Table, Tag, Space, Button, message } from 'antd'
import { DashboardOutlined, ReloadOutlined } from '@ant-design/icons'
import { incidentAPI } from '../services/api'

function RateTag({ v }) {
  const color = v >= 90 ? 'green' : v >= 70 ? 'orange' : 'red'
  return <Tag color={color}>{v}%</Tag>
}

export default function SlaReport() {
  const [rep, setRep] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await incidentAPI.slaReport()
      setRep(d)
    } catch (e) {
      message.error('加载 SLA 报表失败: ' + e.message)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const o = (rep && rep.overall) || {}
  const byCat = (rep && rep.by_category) || []

  const columns = [
    { title: '类别', dataIndex: 'category' },
    { title: '事故数', dataIndex: 'count' },
    { title: '已解决', dataIndex: 'resolved_count' },
    { title: 'MTTD(s)', dataIndex: 'mttd_sec', render: (v) => v ?? '-' },
    { title: 'MTTR(s)', dataIndex: 'mttr_sec', render: (v) => v ?? '-' },
    { title: '1min发现达标', dataIndex: 'detect_ok_rate', render: (v) => <RateTag v={v} /> },
    { title: '5min方案达标', dataIndex: 'plan_ok_rate', render: (v) => <RateTag v={v} /> },
    { title: '10min解决达标', dataIndex: 'resolve_ok_rate', render: (v) => <RateTag v={v} /> },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2><DashboardOutlined /> SLA 报表 (1-5-10)</h2>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}><Card><Statistic title="MTTD 平均发现耗时" value={o.mttd_sec ?? '-'} suffix="s" /></Card></Col>
        <Col span={8}><Card><Statistic title="MTTA 平均确认耗时" value={o.mtta_sec ?? '-'} suffix="s" /></Card></Col>
        <Col span={8}><Card><Statistic title="MTTR 平均解决耗时" value={o.mttr_sec ?? '-'} suffix="s" /></Card></Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}><Card><Statistic title="1分钟发现达标率" value={o.detect_ok_rate ?? 0} suffix="%"
          valueStyle={{ color: (o.detect_ok_rate ?? 0) >= 90 ? '#3f8600' : '#cf1322' }} /></Card></Col>
        <Col span={8}><Card><Statistic title="5分钟方案达标率" value={o.plan_ok_rate ?? 0} suffix="%"
          valueStyle={{ color: (o.plan_ok_rate ?? 0) >= 90 ? '#3f8600' : '#cf1322' }} /></Card></Col>
        <Col span={8}><Card><Statistic title="10分钟解决达标率" value={o.resolve_ok_rate ?? 0} suffix="%"
          valueStyle={{ color: (o.resolve_ok_rate ?? 0) >= 90 ? '#3f8600' : '#cf1322' }} /></Card></Col>
      </Row>
      <Card title="分类别 SLA" loading={loading}>
        <Table rowKey="category" dataSource={byCat} columns={columns} pagination={false} size="small" />
      </Card>
      {rep && <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
        统计范围: {new Date(rep.range[0]).toLocaleString('zh-CN')} ~ {new Date(rep.range[1]).toLocaleString('zh-CN')}
        (仅统计承诺 1-5-10 的 8 类问题)
      </div>}
    </div>
  )
}
