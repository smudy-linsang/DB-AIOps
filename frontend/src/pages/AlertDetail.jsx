import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Tabs, Spin, Alert, Button, Tag, Statistic, Row, Col, Space, Descriptions, message } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined, BulbOutlined, AlertOutlined, ThunderboltOutlined, FileSearchOutlined } from '@ant-design/icons'
import { alertRcaAPI } from '../services/api'

const { TabPane } = Tabs

/**
 * 告警详情页 - Phase 5 P0-6
 * 集成 RCA 2.0 + 健康度影响 + 业务影响 + 方案生成 + 案例匹配
 */
const AlertDetail = () => {
  const { id } = useParams()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('rca')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await alertRcaAPI.getDetail(id)
      setData(result)
    } catch (e) {
      setError(e.message)
      message.error('加载告警详情失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  if (loading) {
    return (
      <div style={{ padding: 50, textAlign: 'center' }}>
        <Spin size="large" tip="加载告警详情..." />
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

  if (!data) {
    return <Alert type="warning" message="无数据" />
  }

  return (
    <div style={{ padding: 24 }}>
      {/* 顶部标题区 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          <h2 style={{ margin: 0 }}>
            <AlertOutlined style={{ color: '#ff4d4f' }} /> 告警详情 #{id}
          </h2>
          {data.alert && (
            <Tag color={getSeverityColor(data.alert.severity)}>
              {data.alert.severity || 'unknown'}
            </Tag>
          )}
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchData}>刷新</Button>
      </div>

      {/* 摘要卡片 */}
      {data.alert && (
        <Card style={{ marginBottom: 16 }}>
          <Descriptions title="告警信息" column={3} bordered size="small">
            <Descriptions.Item label="标题">{data.alert.title || 'N/A'}</Descriptions.Item>
            <Descriptions.Item label="指标">{data.alert.metric_key || 'N/A'}</Descriptions.Item>
            <Descriptions.Item label="当前值">{String(data.alert.value || 'N/A')}</Descriptions.Item>
            <Descriptions.Item label="数据库">{data.alert.db_name || data.alert.db_id || 'N/A'}</Descriptions.Item>
            <Descriptions.Item label="触发时间">{data.alert.fired_at || 'N/A'}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={data.alert.status === 'active' ? 'red' : 'green'}>
                {data.alert.status || 'unknown'}
              </Tag>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      {/* 4 大区域 Tab */}
      <Tabs activeKey={activeTab} onChange={setActiveTab} type="card">
        {/* 区域 1: 根因分析 RCA */}
        <TabPane tab={<span><BulbOutlined />根因分析 (RCA)</span>} key="rca">
          <RcaPanel data={data} />
        </TabPane>

        {/* 区域 2: 健康度影响 */}
        <TabPane tab={<span><AlertOutlined />健康度影响</span>} key="health">
          <HealthImpactPanel data={data} />
        </TabPane>

        {/* 区域 3: 业务影响 */}
        <TabPane tab={<span><ThunderboltOutlined />业务影响</span>} key="business">
          <BusinessImpactPanel data={data} />
        </TabPane>

        {/* 区域 4: 解决方案 */}
        <TabPane tab={<span><FileSearchOutlined />解决方案</span>} key="solution">
          <SolutionPanel data={data} />
        </TabPane>
      </Tabs>
    </div>
  )
}

// ==========================================
// 子组件 1: RCA 面板
// ==========================================
const RcaPanel = ({ data }) => {
  if (!data.rca) {
    return <EmptySection tip="暂无根因分析数据" />
  }
  const { diagnoses = [], summary = '' } = data.rca
  return (
    <div>
      {summary && <Alert type="info" message={summary} style={{ marginBottom: 16 }} />}
      {diagnoses.length === 0 ? (
        <EmptySection tip="未匹配到根因规则" />
      ) : (
        diagnoses.map((d, i) => (
          <Card key={i} style={{ marginBottom: 12 }} size="small"
                title={<Space>
                  <Tag color="blue">R{i+1}</Tag>
                  <strong>{d.rule_title || d.rule_id}</strong>
                  <Tag color={d.severity === 'critical' ? 'red' : 'orange'}>{d.severity}</Tag>
                  <span>置信度 {(d.confidence * 100).toFixed(0)}%</span>
                </Space>}>
            <p style={{ margin: 0 }}>{d.description}</p>
            {d.causal_chain && d.causal_chain.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <strong>因果链路:</strong>
                {d.causal_chain.map((step, idx) => (
                  <Tag key={idx} style={{ marginLeft: 4 }}>{step}</Tag>
                ))}
              </div>
            )}
            {d.related_metrics && Object.keys(d.related_metrics).length > 0 && (
              <div style={{ marginTop: 8 }}>
                <strong>关联指标:</strong>
                {Object.entries(d.related_metrics).map(([k, v]) => (
                  <Tag key={k} style={{ marginLeft: 4 }}>{k}: {String(v)}</Tag>
                ))}
              </div>
            )}
          </Card>
        ))
      )}
    </div>
  )
}

// ==========================================
// 子组件 2: 健康度影响面板
// ==========================================
const HealthImpactPanel = ({ data }) => {
  const impact = data.health_impact || {}
  return (
    <Row gutter={16}>
      <Col span={8}>
        <Card>
          <Statistic title="健康度评分" value={impact.score || 0} suffix="/ 100"
                     valueStyle={{ color: (impact.score || 0) >= 80 ? '#3f8600' : '#cf1322' }} />
        </Card>
      </Col>
      <Col span={8}>
        <Card>
          <Statistic title="受影响维度数" value={impact.affected_dimensions || 0} suffix="个" />
        </Card>
      </Col>
      <Col span={8}>
        <Card>
          <Statistic title="预计恢复时长" value={impact.recovery_hours || 0} suffix="小时" />
        </Card>
      </Col>
      <Col span={24} style={{ marginTop: 16 }}>
        <Card title="详细影响">
          {impact.dimensions && impact.dimensions.length > 0 ? (
            impact.dimensions.map((dim, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <Tag color="blue">{dim.name}</Tag>
                <span>影响程度: <Tag color={dim.severity === 'critical' ? 'red' : 'orange'}>{dim.severity}</Tag></span>
                <span style={{ marginLeft: 8 }}>衰减: {dim.degradation_pct}%</span>
              </div>
            ))
          ) : (
            <EmptySection tip="无详细影响数据" />
          )}
        </Card>
      </Col>
    </Row>
  )
}

// ==========================================
// 子组件 3: 业务影响面板
// ==========================================
const BusinessImpactPanel = ({ data }) => {
  const biz = data.business_impact || {}
  return (
    <Row gutter={16}>
      <Col span={6}>
        <Card><Statistic title="受影响业务系统" value={biz.affected_systems || 0} suffix="个" /></Card>
      </Col>
      <Col span={6}>
        <Card><Statistic title="预计损失" value={biz.estimated_loss_yuan || 0} prefix="¥" /></Card>
      </Col>
      <Col span={6}>
        <Card><Statistic title="SLA 风险" value={biz.sla_risk_pct || 0} suffix="%" /></Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic title="影响用户数" value={biz.affected_users || 0}
                     valueStyle={{ color: '#cf1322' }} />
        </Card>
      </Col>
      <Col span={24} style={{ marginTop: 16 }}>
        <Card title="受影响业务系统">
          {biz.systems && biz.systems.length > 0 ? (
            biz.systems.map((sys, i) => (
              <Card.Grid key={i} style={{ width: '33.33%', textAlign: 'center' }}>
                <strong>{sys.name}</strong><br />
                <Tag color={sys.importance === 'high' ? 'red' : 'orange'}>
                  重要度: {sys.importance || 'normal'}
                </Tag><br />
                <small>影响范围: {sys.impact_scope || 'N/A'}</small>
              </Card.Grid>
            ))
          ) : (
            <EmptySection tip="无业务影响数据" />
          )}
        </Card>
      </Col>
    </Row>
  )
}

// ==========================================
// 子组件 4: 解决方案面板
// ==========================================
const SolutionPanel = ({ data }) => {
  const plan = data.remediation_plan || {}
  const cases = data.similar_cases || []

  if (!plan.scenarios && cases.length === 0) {
    return <EmptySection tip="暂无可用方案" />
  }

  return (
    <div>
      {/* 三套方案 */}
      {plan.scenarios && plan.scenarios.length > 0 && (
        <Card title="处置方案" style={{ marginBottom: 16 }}>
          <Tabs type="card">
            {plan.scenarios.map((sc, i) => (
              <TabPane tab={
                <Space>
                  <Tag color={sc.name === 'conservative' ? 'green' : sc.name === 'standard' ? 'blue' : 'red'}>
                    {sc.name === 'conservative' ? '保守' : sc.name === 'standard' ? '标准' : '激进'}
                  </Tag>
                  <span>风险: {sc.risk_level}</span>
                </Space>
              } key={sc.name || i}>
                <Alert
                  type={sc.risk_level === 'critical' ? 'error' : sc.risk_level === 'high' ? 'warning' : 'info'}
                  message={`预估耗时:${sc.estimated_minutes || 0} 分钟  需审批:${sc.require_approval ? '是' : '否'}`}
                  style={{ marginBottom: 12 }}
                />
                {sc.steps && sc.steps.map((step, idx) => (
                  <Card key={idx} size="small" style={{ marginBottom: 8 }}
                        title={`步骤 ${idx + 1}: ${step.action || '操作'}`}>
                    <p>{step.description}</p>
                    {step.sql && (
                      <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, overflow: 'auto' }}>
                        {step.sql}
                      </pre>
                    )}
                    {step.rollback && (
                      <Alert type="warning" message={
                        <span><strong>回滚方案:</strong> {step.rollback}</span>
                      } />
                    )}
                  </Card>
                ))}
                {sc.requires_approval && (
                  <Button type="primary" danger>提交审批</Button>
                )}
              </TabPane>
            ))}
          </Tabs>
        </Card>
      )}

      {/* 历史案例 */}
      {cases.length > 0 && (
        <Card title="相似历史案例">
          {cases.map((c, i) => (
            <Card.Grid key={i} style={{ width: '50%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space>
                  <Tag color="cyan">{c.case_id}</Tag>
                  <strong>{c.title}</strong>
                  <Tag color="green">相似度 {(c.similarity * 100).toFixed(0)}%</Tag>
                </Space>
                <div><strong>根因:</strong> {c.root_cause}</div>
                <div><strong>方案:</strong> {c.resolution}</div>
                <div style={{ color: '#999', fontSize: 12 }}>成功使用 {c.success_count || 0} 次</div>
              </Space>
            </Card.Grid>
          ))}
        </Card>
      )}
    </div>
  )
}

// ==========================================
// 工具组件
// ==========================================
const EmptySection = ({ tip }) => (
  <Alert type="info" message={tip} showIcon />
)

const getSeverityColor = (sev) => {
  return { critical: 'red', warning: 'orange', info: 'blue' }[sev] || 'default'
}

export default AlertDetail
