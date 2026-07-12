import React, { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Select, Space, Button, message } from 'antd'
import { ThunderboltOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { incidentAPI } from '../services/api'

const PRIORITY_COLOR = { P1: 'red', P2: 'orange', P3: 'blue', P4: 'default' }
const STATUS_COLOR = {
  open: 'red', diagnosing: 'processing', plan_ready: 'gold',
  executing: 'purple', verifying: 'cyan', resolved: 'green', closed: 'default',
}
const CATEGORY_LABEL = {
  availability: '可用性', lock: '锁', connection: '连接', capacity: '容量',
  replication: '复制', performance: '性能', config: '配置', other: '其他',
}

export default function IncidentList() {
  const navigate = useNavigate()
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await incidentAPI.list(filters)
      setRows(Array.isArray(d) ? d : (d.incidents || []))
    } catch (e) {
      message.error('加载事故失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { load() }, [load])

  const columns = [
    {
      title: '事故ID', dataIndex: 'incident_id', width: 210,
      render: (id) => <a onClick={() => navigate(`/incidents/${id}`)}>{id}</a>,
    },
    { title: '数据库', dataIndex: 'db_name' },
    {
      title: '类别', dataIndex: 'category',
      render: (c) => <Tag>{CATEGORY_LABEL[c] || c}</Tag>,
    },
    {
      title: '优先级', dataIndex: 'priority', width: 80,
      render: (p) => <Tag color={PRIORITY_COLOR[p]}>{p}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (s) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag>,
    },
    { title: '事件数', dataIndex: 'event_count', width: 70 },
    {
      title: '发现耗时', dataIndex: 't_detect_sec', width: 100,
      render: (v, r) => v == null ? '-' :
        <Tag color={r.sla_detect_ok ? 'green' : 'red'}>{v}s</Tag>,
    },
    {
      title: '标记', key: 'flags', width: 90,
      render: (_, r) => (
        <Space size={2}>
          {r.is_storm && <Tag color="volcano">风暴</Tag>}
          {r.is_flapping && <Tag color="magenta">抖动</Tag>}
        </Space>
      ),
    },
    {
      title: '发生时间', dataIndex: 'occurred_at', width: 170,
      render: (t) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作', key: 'action', width: 80,
      render: (_, r) => <Button size="small" onClick={() => navigate(`/incidents/${r.incident_id}`)}>详情</Button>,
    },
  ]

  const setF = (k) => (v) => setFilters((f) => ({ ...f, [k]: v }))

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2><ThunderboltOutlined /> 事故中心</h2>
        <Space>
          <Select allowClear placeholder="优先级" style={{ width: 110 }} onChange={setF('priority')}
            options={['P1', 'P2', 'P3', 'P4'].map(v => ({ value: v, label: v }))} />
          <Select allowClear placeholder="状态" style={{ width: 130 }} onChange={setF('status')}
            options={Object.keys(STATUS_COLOR).map(v => ({ value: v, label: v }))} />
          <Select allowClear placeholder="类别" style={{ width: 120 }} onChange={setF('category')}
            options={Object.entries(CATEGORY_LABEL).map(([v, l]) => ({ value: v, label: l }))} />
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
      </div>
      <Card>
        <Table rowKey="incident_id" loading={loading} dataSource={rows} columns={columns}
          pagination={{ pageSize: 20 }} size="small" />
      </Card>
    </div>
  )
}
