import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Card, Tabs, Spin, Alert, Button, Tag, Statistic, Row, Col, Space, Descriptions,
  Progress, Table, message
} from 'antd'
import {
  ArrowLeftOutlined, ReloadOutlined, FileTextOutlined,
  CheckCircleOutlined, WarningOutlined, CloseCircleOutlined, BulbOutlined
} from '@ant-design/icons'
import { inspectionAPI } from '../services/api'

const { TabPane } = Tabs

/**
 * 巡检详情页 - Phase 5 P1-6
 * 展示单次巡检的完整报告
 */
const InspectionDetail = () => {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [run, setRun] = useState(null)
  const [findings, setFindings] = useState([])
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await inspectionAPI.getRunDetail(runId)
      setRun(data.run || data)
      setFindings(data.findings || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  if (loading) {
    return (
      <div style={{ padding: 50, textAlign: 'center' }}>
        <Spin size="large" tip="加载巡检报告..." />
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="error" message="加载失败" description={error} showIcon />
        <Button onClick={fetchData} style={{ marginTop: 16 }}>重试</Button>
      </div>
    )
  }

  if (!run) {
    return <Alert type="warning" message="无数据" />
  }

  const score = run.health_score || 0
  const scoreColor = score >= 90 ? '#52c41a' : score >= 70 ? '#faad14' : '#f5222d'
  const scoreText = score >= 90 ? '健康' : score >= 70 ? '注意' : '风险'

  const findingColumns = [
    { title: '编号', dataIndex: 'item_code', key: 'item_code', width: 100 },
    { title: '巡检项', dataIndex: 'item_title', key: 'item_title' },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s) => {
        const config = {
          critical: { color: 'red', icon: <CloseCircleOutlined /> },
          warning: { color: 'orange', icon: <WarningOutlined /> },
          ok: { color: 'green', icon: <CheckCircleOutlined /> },
          error: { color: 'grey', icon: <CloseCircleOutlined /> },
          skip: { color: 'default', icon: null },
        }
        const c = config[s] || { color: 'default', icon: null }
        return <Tag color={c.color} icon={c.icon}>{s}</Tag>
      },
    },
    {
      title: '严重度', dataIndex: 'severity', key: 'severity', width: 100,
      render: (s) => {
        const colors = { critical: 'red', warning: 'orange', info: 'blue' }
        return <Tag color={colors[s] || 'default'}>{s}</Tag>
      },
    },
    {
      title: '摘要', dataIndex: 'summary', key: 'summary',
      ellipsis: true,
    },
    {
      title: '耗时', dataIndex: 'duration_ms', key: 'duration',
      width: 80, render: (d) => d ? `${d}ms` : '-',
    },
  ]

  // 解析 finding details JSON
  const expandedRowRender = (record) => {
    const details = record.details || {}
    return (
      <div style={{ padding: 12, background: '#fafafa' }}>
        {details.findings && details.findings.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <strong>发现:</strong>
            {details.findings.map((f, i) => (
              <div key={i} style={{ marginTop: 4, paddingLeft: 12 }}>
                <Tag>{f.type}</Tag>
                {f.message}
              </div>
            ))}
          </div>
        )}
        {details.metrics && Object.keys(details.metrics).length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <strong>指标:</strong>
            <pre style={{ background: '#fff', padding: 8, marginTop: 4, fontSize: 12, maxHeight: 200, overflow: 'auto' }}>
              {JSON.stringify(details.metrics, null, 2)}
            </pre>
          </div>
        )}
        {details.error && (
          <Alert type="error" message={details.error} />
        )}
        {record.detection_method && (
          <div style={{ color: '#999', fontSize: 12, marginTop: 8 }}>
            检测方法: {record.detection_method} | 置信度: {record.confidence || 0}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ padding: 24 }}>
      {/* 顶部 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          <h2 style={{ margin: 0 }}><FileTextOutlined /> 巡检报告</h2>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchData}>刷新</Button>
      </div>

      {/* 报告元信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Descriptions title={`报告 ${run.run_id}`} column={3} bordered size="small">
          <Descriptions.Item label="数据库">{run.db_config?.name} ({run.db_config?.db_type})</Descriptions.Item>
          <Descriptions.Item label="巡检级别">
            <Tag color={{daily:'blue', weekly:'green', monthly:'purple'}[run.level]}>{run.level}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={run.status === 'completed' ? 'green' : 'orange'}>{run.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="开始时间">{run.started_at ? new Date(run.started_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          <Descriptions.Item label="完成时间">{run.completed_at ? new Date(run.completed_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          <Descriptions.Item label="耗时">{run.duration_sec}s</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 健康度评分 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic title="健康度评分" value={score} suffix="/ 100"
                       valueStyle={{ color: scoreColor }} />
            <div style={{ marginTop: 8, color: scoreColor, fontWeight: 'bold' }}>{scoreText}</div>
          </Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="总项数" value={run.total_items || 0} /></Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="问题项" value={(run.critical_count || 0) + (run.warning_count || 0)}
                       valueStyle={{ color: '#cf1322' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="健康项" value={run.ok_count || 0}
                            valueStyle={{ color: '#3f8600' }} /></Card>
        </Col>
      </Row>

      {/* 分布条 */}
      <Card style={{ marginBottom: 16 }} title="结果分布">
        <Row gutter={8}>
          <Col span={6}>
            <Progress percent={Math.round((run.critical_count || 0) / Math.max(run.total_items, 1) * 100)}
                      strokeColor="#f5222d" format={() => `严重 ${run.critical_count || 0}`} />
          </Col>
          <Col span={6}>
            <Progress percent={Math.round((run.warning_count || 0) / Math.max(run.total_items, 1) * 100)}
                      strokeColor="#faad14" format={() => `警告 ${run.warning_count || 0}`} />
          </Col>
          <Col span={6}>
            <Progress percent={Math.round((run.ok_count || 0) / Math.max(run.total_items, 1) * 100)}
                      strokeColor="#52c41a" format={() => `正常 ${run.ok_count || 0}`} />
          </Col>
          <Col span={6}>
            <Progress percent={Math.round((run.error_count || 0) / Math.max(run.total_items, 1) * 100)}
                      strokeColor="#8c8c8c" format={() => `错误 ${run.error_count || 0}`} />
          </Col>
        </Row>
      </Card>

      {/* 详细发现列表 */}
      <Card title="巡检发现">
        <Table
          dataSource={findings}
          columns={findingColumns}
          rowKey="finding_id"
          pagination={{ pageSize: 20 }}
          expandable={{ expandedRowRender }}
          defaultExpandAllRows={false}
        />
      </Card>
    </div>
  )
}

export default InspectionDetail
