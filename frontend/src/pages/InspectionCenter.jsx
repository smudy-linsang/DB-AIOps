import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Card, Table, Button, Space, Tag, Select, Input, Modal, Form,
  Statistic, Row, Col, Tabs, Alert, message, Spin, Progress, Tooltip
} from 'antd'
import {
  PlayCircleOutlined, ReloadOutlined, FileTextOutlined,
  BulbOutlined, CheckCircleOutlined, WarningOutlined,
  CloseCircleOutlined, DatabaseOutlined, ClockCircleOutlined
} from '@ant-design/icons'
import { inspectionAPI } from '../services/api'

const { Option } = Select
const { TabPane } = Tabs

/**
 * 巡检中心页面 - Phase 5 P1-6
 * 提供巡检执行、查看、报告生成
 */
const InspectionCenter = () => {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('runs')
  const [runs, setRuns] = useState([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [items, setItems] = useState([])
  const [itemsLoading, setItemsLoading] = useState(false)
  const [patterns, setPatterns] = useState([])
  const [patternsLoading, setPatternsLoading] = useState(false)
  const [triggerVisible, setTriggerVisible] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [form] = Form.useForm()

  // 加载巡检执行记录
  const loadRuns = useCallback(async () => {
    setRunsLoading(true)
    try {
      const data = await inspectionAPI.listRuns()
      setRuns(data.results || data || [])
    } catch (e) {
      message.error('加载巡检记录失败: ' + e.message)
    } finally {
      setRunsLoading(false)
    }
  }, [])

  // 加载巡检项定义
  const loadItems = useCallback(async () => {
    setItemsLoading(true)
    try {
      const data = await inspectionAPI.listItems()
      setItems(data.results || data || [])
    } catch (e) {
      message.error('加载巡检项失败: ' + e.message)
    } finally {
      setItemsLoading(false)
    }
  }, [])

  // 加载问题模式
  const loadPatterns = useCallback(async () => {
    setPatternsLoading(true)
    try {
      const data = await inspectionAPI.listPatterns()
      setPatterns(data.results || data || [])
    } catch (e) {
      message.error('加载问题模式失败: ' + e.message)
    } finally {
      setPatternsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRuns()
    loadItems()
    loadPatterns()
  }, [loadRuns, loadItems, loadPatterns])

  // 触发巡检
  const handleTrigger = async () => {
    try {
      const values = await form.validateFields()
      setTriggering(true)
      const result = await inspectionAPI.triggerRun(values)
      message.success(`巡检已启动: ${result.run_id || '成功'}`)
      setTriggerVisible(false)
      form.resetFields()
      loadRuns()
    } catch (e) {
      if (e.errorFields) return
      message.error('触发失败: ' + e.message)
    } finally {
      setTriggering(false)
    }
  }

  // 表格列定义
  const runColumns = [
    {
      title: '巡检ID',
      dataIndex: 'run_id',
      key: 'run_id',
      width: 220,
      render: (id) => (
        <a onClick={() => navigate(`/inspection/runs/${id}`)}>{id}</a>
      ),
    },
    {
      title: '数据库',
      dataIndex: ['db_config', 'name'],
      key: 'db',
      render: (name, r) => (
        <Space>
          <DatabaseOutlined />
          {name || r.db_config_id}
          <Tag>{r.db_config?.db_type}</Tag>
        </Space>
      ),
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      render: (l) => {
        const colors = { daily: 'blue', weekly: 'green', monthly: 'purple' }
        return <Tag color={colors[l] || 'default'}>{l}</Tag>
      },
    },
    {
      title: '健康度',
      dataIndex: 'health_score',
      key: 'health',
      sorter: (a, b) => (a.health_score || 0) - (b.health_score || 0),
      render: (s) => {
        const v = s || 0
        const color = v >= 90 ? '#52c41a' : v >= 70 ? '#faad14' : '#f5222d'
        return (
          <Tooltip title={`健康度: ${v}/100`}>
            <Progress percent={v} size="small" strokeColor={color} />
          </Tooltip>
        )
      },
    },
    {
      title: '发现统计',
      key: 'stats',
      render: (_, r) => (
        <Space size="small">
          <Tag color="red">严重 {r.critical_count || 0}</Tag>
          <Tag color="orange">警告 {r.warning_count || 0}</Tag>
          <Tag color="green">正常 {r.ok_count || 0}</Tag>
          {r.error_count > 0 && <Tag color="grey">错误 {r.error_count}</Tag>}
        </Space>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'duration_sec',
      key: 'duration',
      render: (d) => d ? `${d}s` : '-',
    },
    {
      title: '执行时间',
      dataIndex: 'started_at',
      key: 'started',
      render: (t) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s) => {
        const map = { running: 'processing', completed: 'success', failed: 'error' }
        return <Tag color={map[s] || 'default'}>{s}</Tag>
      },
    },
    {
      title: '操作',
      key: 'action',
      render: (_, r) => (
        <Button size="small" icon={<FileTextOutlined />}
                onClick={() => navigate(`/inspection/runs/${r.run_id}`)}>
          详情
        </Button>
      ),
    },
  ]

  const itemColumns = [
    { title: '编号', dataIndex: 'item_code', key: 'item_code', width: 80 },
    { title: '标题', dataIndex: 'title', key: 'title' },
    {
      title: '类别', dataIndex: 'category', key: 'category',
      render: (c) => <Tag>{c}</Tag>,
    },
    {
      title: '级别', dataIndex: 'level', key: 'level',
      render: (l) => {
        const colors = { daily: 'blue', weekly: 'green', monthly: 'purple' }
        return <Tag color={colors[l] || 'default'}>{l}</Tag>
      },
    },
    {
      title: '严重度', dataIndex: 'severity', key: 'severity',
      render: (s) => {
        const colors = { critical: 'red', warning: 'orange', info: 'blue' }
        return <Tag color={colors[s] || 'default'}>{s}</Tag>
      },
    },
    {
      title: '适用数据库', dataIndex: 'applicable_db_types', key: 'dbs',
      render: (dbs) => (dbs || []).map(d => <Tag key={d}>{d}</Tag>),
    },
    {
      title: '可自动修复', dataIndex: 'auto_fixable', key: 'auto',
      render: (a) => a ? <Tag color="green">是</Tag> : <Tag>否</Tag>,
    },
  ]

  const patternColumns = [
    { title: '巡检项', dataIndex: 'item_title', key: 'item_title' },
    { title: '编号', dataIndex: 'item_code', key: 'item_code', width: 100 },
    {
      title: '严重等级', dataIndex: 'severity', key: 'severity',
      render: (s) => {
        const colors = { P0: 'red', P1: 'orange', P2: 'blue', P3: 'default' }
        return <Tag color={colors[s] || 'default'}>{s}</Tag>
      },
    },
    { title: '出现次数', dataIndex: 'occurrence_count', key: 'occ', sorter: (a, b) => a.occurrence_count - b.occurrence_count },
    { title: '影响实例', dataIndex: 'affected_instances', key: 'inst' },
    {
      title: '数据库类型', dataIndex: 'db_types', key: 'dbs',
      render: (dbs) => (dbs || []).map(d => <Tag key={d}>{d}</Tag>),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h2><CheckCircleOutlined /> 智能巡检中心</h2>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => { loadRuns(); loadItems(); loadPatterns() }}>
            刷新
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setTriggerVisible(true)}>
            触发巡检
          </Button>
        </Space>
      </div>

      <Tabs activeKey={activeTab} onChange={setActiveTab}>
        <TabPane tab={<span><ClockCircleOutlined />巡检记录</span>} key="runs">
          <Table
            loading={runsLoading}
            dataSource={runs}
            columns={runColumns}
            rowKey="run_id"
            pagination={{ pageSize: 20 }}
          />
        </TabPane>
        <TabPane tab={<span><BulbOutlined />巡检项定义</span>} key="items">
          <Alert type="info" message={`共 ${items.length} 项巡检规则,涵盖 Oracle/MySQL/PG/DM/GBase/TDSQL 6 类数据库`}
                 style={{ marginBottom: 16 }} showIcon />
          <Table loading={itemsLoading} dataSource={items} columns={itemColumns} rowKey="item_code"
                 pagination={{ pageSize: 30 }} />
        </TabPane>
        <TabPane tab={<span><WarningOutlined />问题模式</span>} key="patterns">
          <Alert type="warning" message="系统自动识别的共性问题模式,推荐优先处理" style={{ marginBottom: 16 }} />
          <Table loading={patternsLoading} dataSource={patterns} columns={patternColumns} rowKey="id" />
        </TabPane>
      </Tabs>

      {/* 触发巡检对话框 */}
      <Modal title="触发巡检" open={triggerVisible} onCancel={() => setTriggerVisible(false)}
             onOk={handleTrigger} confirmLoading={triggering} okText="启动">
        <Form form={form} layout="vertical" initialValues={{ level: 'daily' }}>
          <Form.Item label="数据库 ID" name="db_id" tooltip="留空则对所有数据库巡检">
            <Input placeholder="留空表示全部" allowClear />
          </Form.Item>
          <Form.Item label="巡检级别" name="level" rules={[{ required: true }]}>
            <Select>
              <Option value="daily">日检 (基础项)</Option>
              <Option value="weekly">周检 (深度项)</Option>
              <Option value="monthly">月检 (综合项)</Option>
            </Select>
          </Form.Item>
          <Form.Item label="指定项" name="item_ids" tooltip="留空则按级别全部执行">
            <Select mode="tags" placeholder="可输入 I001,I002 等编号" allowClear />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default InspectionCenter
