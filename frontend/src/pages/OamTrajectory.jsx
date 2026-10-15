import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Card, Table, Tag, Alert, Descriptions, Button, Space, Spin, Empty, Tooltip, Typography
} from 'antd'
import {
  NodeIndexOutlined, ReloadOutlined, ArrowLeftOutlined, SafetyCertificateOutlined
} from '@ant-design/icons'
import { oamAPI } from '../services/api'
import {
  reportState, verdictState, auditState, toolOutcomeState, describeError
} from '../utils/oamSemantics'

const { Text, Paragraph } = Typography

/**
 * 轨迹与证据审计（V3.0 设计 §6）
 *
 * 两个只读接口：
 *   GET /api/v3/oam/trajectories/<traj_id>/        完整思考-行动-观察轨迹
 *   GET /api/v3/oam/trajectories/<traj_id>/audit/  证据审计结果
 *
 * 两条要注意的语义：
 *   · `escalated=true` + `evidence_insufficient` 是合法终止（warning，不是报错）；
 *   · 越权与不存在统一 404，文案只能写"记录不存在或无权访问"。
 */
const OamTrajectory = () => {
  const { trajId } = useParams()
  const navigate = useNavigate()
  const [traj, setTraj] = useState(null)
  const [auditData, setAuditData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [auditErr, setAuditErr] = useState(null)

  const load = useCallback(async () => {
    if (!trajId) return
    setLoading(true)
    setErr(null)
    setAuditErr(null)
    try {
      const [trajRes, auditRes] = await Promise.allSettled([
        oamAPI.getTrajectory(trajId),
        oamAPI.getTrajectoryAudit(trajId),
      ])
      if (trajRes.status === 'rejected') throw trajRes.reason
      setTraj(trajRes.value?.data || null)
      if (auditRes.status === 'fulfilled') {
        setAuditData(auditRes.value?.data || null)
      } else {
        // 审计单独失败时不要静默当成"没有不支持项"——那是把一次请求失败
        // 读成了一份干净结论。如实按业务语义呈现
        setAuditData(null)
        setAuditErr(describeError(auditRes.reason?.code, auditRes.reason?.status))
      }
    } catch (e) {
      setTraj(null)
      setAuditData(null)
      setAuditErr(null)
      setErr(describeError(e?.code, e?.status))
    } finally {
      setLoading(false)
    }
  }, [trajId])

  useEffect(() => {
    load()
  }, [load])

  const report = traj?.report || {}
  const state = reportState(report.status, traj?.escalated)
  const audit = auditState(auditData?.audit)
  // 规则 2：三态。轨迹自带的 report 通常没有 verdict 字段（那是 diagnose 侧补的），
  // 缺字段就写"未判定"，不许借 trajectory.passed 这个 bool 去冒充"判了分"
  const verdict = verdictState(report.verdict ? report.verdict.passed : null)
  const turns = Array.isArray(traj?.turns) ? traj.turns : []

  const turnColumns = [
    { title: '#', dataIndex: 'turn', key: 'turn', width: 60 },
    {
      title: '思考', dataIndex: 'thought', key: 'thought', width: '28%',
      render: (t) => (t ? <Text style={{ fontSize: 12 }}>{t}</Text> : <Text type="secondary">—</Text>),
    },
    {
      title: '动作', key: 'action', width: '22%',
      render: (_, r) => (
        <Space direction="vertical" size={2}>
          <Tag>{r.tool_name || '最终报告'}</Tag>
          {r.tool_params && (
            <Text code style={{ fontSize: 11 }}>
              {typeof r.tool_params === 'string' ? r.tool_params : JSON.stringify(r.tool_params)}
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: '工具返回', key: 'outcome', width: 140,
      render: (_, r) => {
        const s = toolOutcomeState(r)
        return <Tag color={s.color}>{s.text}</Tag>
      },
    },
    {
      title: '观察', dataIndex: 'observation', key: 'observation',
      render: (o, r) => (
        <Space direction="vertical" size={2}>
          <Text style={{ fontSize: 12 }}>
            {typeof o === 'string' ? o : o ? JSON.stringify(o) : '—'}
          </Text>
          {r.observation_evidence && (
            <Tooltip title="本轮观测的证据引用">
              <Text type="secondary" style={{ fontSize: 11 }}>
                证据：{JSON.stringify(r.observation_evidence)}
              </Text>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  const unsupported = Array.isArray(auditData?.audit?.unsupported) ? auditData.audit.unsupported : []

  const unsupportedColumns = [
    { title: '类型', dataIndex: 'kind', key: 'kind', width: 140, render: (k) => <Tag>{k || '-'}</Tag> },
    { title: '无证据支撑的主张', dataIndex: 'text', key: 'text' },
    {
      title: '证据引用', dataIndex: 'evidence_refs', key: 'refs', width: 220,
      render: (refs) => ((refs || []).length
        ? <Space size={4} wrap>{(refs || []).map((r, i) => <Tag key={i} color="blue">{r}</Tag>)}</Space>
        : <Text type="secondary">无</Text>),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}><NodeIndexOutlined /> 轨迹与证据审计</h2>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/oam/diagnose')}>返回诊断</Button>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
      </div>

      <Card size="small" title={`轨迹 ${trajId || '-'}`} style={{ marginBottom: 16 }}>
        <Spin spinning={loading}>
          {err && (
            <Alert
              type={err.kind}
              showIcon
              message={err.text}
              description={err.ambiguous ? '系统不区分「不存在」与「无权访问」，这是有意为之。' : undefined}
            />
          )}

          {!err && !traj && !loading && <Empty description="未找到轨迹数据" />}

          {traj && (
            <>
              <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
                <Descriptions.Item label="用例">{traj.case_id || '-'}</Descriptions.Item>
                <Descriptions.Item label="策略">
                  {traj.policy_backend || '-'}{traj.policy_version ? ` / ${traj.policy_version}` : ''}
                </Descriptions.Item>
                <Descriptions.Item label="开始">{traj.started_at || '-'}</Descriptions.Item>
                <Descriptions.Item label="结束">{traj.finished_at || '-'}</Descriptions.Item>
                <Descriptions.Item label="是否转人工">
                  <Tag color={traj.escalated ? 'orange' : 'default'}>
                    {traj.escalated ? '是（合法终止不计成功）' : '否'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="是否幻觉">
                  <Tag color={traj.hallucinated ? 'red' : 'green'}>
                    {traj.hallucinated ? '存在幻觉' : '无'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="轨迹判定">
                  {/* 规则 2：三态。null 表示"没判分"，不是"判了没过" */}
                  {report.verdict
                    ? <Tag color={verdict.color}>{verdict.text}</Tag>
                    : <Tag color="blue">未判定</Tag>}
                </Descriptions.Item>
                <Descriptions.Item label="结论">
                  <Tag color={state.color}>{state.text}</Tag>
                </Descriptions.Item>
              </Descriptions>

              {state.legalStop && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="证据不足，已转人工复核"
                  description="合法终止：不计成功，也不计幻觉。后端在证据审计未通过时不给出根因，这里不把空根因当结论。"
                />
              )}

              <Card size="small" type="inner" title="最终报告" style={{ marginBottom: 16 }}>
                <Paragraph style={{ marginBottom: 8 }}>
                  <Text strong>根因：</Text>
                  {report.root_cause
                    ? <Text strong>{report.root_cause}</Text>
                    : <Text type="secondary">未给出（证据不足时不臆造根因）</Text>}
                </Paragraph>
                <Paragraph style={{ marginBottom: 8 }}>
                  <Text strong>修复建议：</Text>
                  <Text>{report.fix || '无'}</Text>
                </Paragraph>
                {report.reason && (
                  <Paragraph style={{ marginBottom: 0 }}>
                    <Text strong>说明：</Text>
                    <Text>{report.reason}</Text>
                  </Paragraph>
                )}
              </Card>
            </>
          )}
        </Spin>
      </Card>

      <Card size="small" title={<span><SafetyCertificateOutlined /> 证据审计</span>} style={{ marginBottom: 16 }}>
        <Spin spinning={loading}>
          {!err && (
            <>
              {auditErr ? (
                <Alert
                  type={auditErr.kind}
                  showIcon
                  message={`证据审计未取到：${auditErr.text}`}
                  description={auditErr.ambiguous ? '系统不区分「不存在」与「无权访问」，这是有意为之。' : undefined}
                />
              ) : (
                <>
                  <Alert
                    type={audit.kind}
                    showIcon
                    style={{ marginBottom: 12 }}
                    message={audit.text}
                    description={audit.reason || undefined}
                  />
                  {unsupported.length > 0 ? (
                    <Table
                      size="small"
                      rowKey={(r, i) => `${r.kind}-${i}`}
                      dataSource={unsupported}
                      columns={unsupportedColumns}
                      pagination={false}
                    />
                  ) : (
                    <Text type="secondary">无无证据支撑的主张</Text>
                  )}
                </>
              )}
            </>
          )}
        </Spin>
      </Card>

      <Card size="small" title="思考-行动-观察轨迹">
        <Table
          size="small"
          rowKey={(r, i) => `${r.turn}-${i}`}
          dataSource={turns}
          columns={turnColumns}
          loading={loading}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="该轨迹没有记录任何轮次" /> }}
        />
      </Card>
    </div>
  )
}

export default OamTrajectory
