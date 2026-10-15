import React, { useState, useEffect, useCallback } from 'react'
import {
  Card, Table, Tag, Alert, Button, Space, Statistic, Row, Col, Spin, message, Typography, Empty
} from 'antd'
import {
  BookOutlined, ReloadOutlined, SyncOutlined, WarningOutlined, FileTextOutlined
} from '@ant-design/icons'
import { oamAPI } from '../services/api'
import { stalenessState, describeError } from '../utils/oamSemantics'

const { Text, Paragraph } = Typography

/**
 * KB 新鲜度看板（V3.0 设计 §6）
 *
 *   GET  /api/v3/oam/kb/staleness/   新鲜度看板（阈值分布 + 陈旧清单）
 *   GET  /api/v3/oam/kb/documents/   KB 文档列表
 *   POST /api/v3/oam/kb/documents/   **恒 501**（P0 未开放直接写入）
 *   POST /api/v3/oam/kb/rebuild/     **恒 501**（须走 oam_kb_rebuild 管理命令）
 *
 * 两个 501 是后端刻意为之（写入须经"人工复核 + 去标识 + 带版本审批"闸门），
 * 所以这里如实呈现"后端未从 HTTP 暴露该操作"，而不是把它当成故障重试。
 */
const OamKb = () => {
  const [staleness, setStaleness] = useState(null)
  const [docs, setDocs] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [rebuildErr, setRebuildErr] = useState(null)
  const [rebuilding, setRebuilding] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    const [st, dc] = await Promise.allSettled([
      oamAPI.kbStaleness(),
      oamAPI.kbDocuments(),
    ])
    if (st.status === 'rejected') {
      setErr(describeError(st.reason?.code, st.reason?.status))
      setStaleness(null)
    } else {
      setStaleness(st.value?.data || null)
    }
    if (dc.status === 'rejected') {
      setDocs(null)
    } else {
      setDocs(dc.value?.data || null)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleRebuild = async () => {
    setRebuilding(true)
    setRebuildErr(null)
    try {
      await oamAPI.kbRebuild()
      message.success('已触发重建')
    } catch (e) {
      // 501 是后端设计（走管理命令），不是故障 —— 按业务语义呈现
      setRebuildErr(describeError(e?.code, e?.status))
    } finally {
      setRebuilding(false)
    }
  }

  const threshold = staleness?.threshold_days
  const byCategory = staleness?.by_category || {}
  const staleItems = Array.isArray(staleness?.stale_items) ? staleness.stale_items : []
  const items = Array.isArray(docs?.items) ? docs.items : []

  const categoryColumns = [
    { title: '类别', dataIndex: 'category', key: 'category' },
    { title: '文档数', dataIndex: 'count', key: 'count', width: 100 },
    {
      title: '最大陈旧天数', dataIndex: 'max_days', key: 'max_days', width: 140,
      render: (v) => <Tag color={stalenessState(v, threshold).color}>{v} 天</Tag>,
    },
    {
      title: '超阈值文档数', dataIndex: 'over_threshold', key: 'over', width: 130,
      render: (v) => (v > 0 ? <Tag color="orange">{v}</Tag> : <Tag color="green">0</Tag>),
    },
  ]

  const staleColumns = [
    { title: '文档 ID', dataIndex: 'doc_id', key: 'doc_id', width: 220 },
    { title: '标题', dataIndex: 'title', key: 'title' },
    {
      title: '陈旧天数', dataIndex: 'staleness_days', key: 'staleness_days', width: 160,
      render: (v) => {
        const s = stalenessState(v, threshold)
        return <Tag color={s.color}>{s.text}</Tag>
      },
    },
    { title: '审批人', dataIndex: 'approved_by', key: 'approved_by', width: 140 },
  ]

  const docColumns = [
    { title: '文档 ID', dataIndex: 'doc_id', key: 'doc_id', width: 220 },
    { title: '标题', dataIndex: 'title', key: 'title' },
    { title: '类别', dataIndex: 'category', key: 'category', width: 140, render: (c) => <Tag>{c}</Tag> },
    {
      title: '适用方言', dataIndex: 'db_types', key: 'db_types', width: 200,
      render: (d) => ((d || []).length
        ? <Space size={4} wrap>{(d || []).map((t) => <Tag key={t} color="blue">{t}</Tag>)}</Space>
        : <Text type="secondary">全部</Text>),
    },
    { title: '版本', dataIndex: 'version', key: 'version', width: 90 },
    { title: '审批人', dataIndex: 'approved_by', key: 'approved_by', width: 130 },
    {
      title: '新鲜度', dataIndex: 'staleness_days', key: 'staleness_days', width: 170,
      render: (v) => {
        const s = stalenessState(v, threshold)
        return <Tag color={s.color}>{s.text}</Tag>
      },
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}><BookOutlined /> KB 新鲜度看板</h2>
        <Space>
          <Button icon={<SyncOutlined />} loading={rebuilding} onClick={handleRebuild}>触发重建</Button>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
      </div>

      {err && (
        <Alert
          type={err.kind}
          showIcon
          style={{ marginBottom: 16 }}
          message={err.text}
          description={err.ambiguous ? '系统不区分「不存在」与「无权访问」，这是有意为之。' : undefined}
        />
      )}

      {rebuildErr && (
        <Alert
          type={rebuildErr.kind}
          showIcon
          style={{ marginBottom: 16 }}
          message={rebuildErr.text}
          description="KB 重建须经版本化产物与审批链，由管理命令 oam_kb_rebuild 执行；P0 阶段后端刻意不从 HTTP 暴露该操作。"
        />
      )}

      <Spin spinning={loading}>
        {!staleness && !err && <Empty description="暂无 KB 数据" />}

        {staleness && (
          <>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={6}><Card size="small"><Statistic title="文档总数" value={staleness.total_docs || 0} /></Card></Col>
              <Col span={6}>
                <Card size="small">
                  <Statistic
                    title="新鲜度阈值（天）"
                    value={threshold ?? '-'}
                    prefix={<WarningOutlined style={{ color: '#faad14' }} />}
                  />
                </Card>
              </Col>
              <Col span={6}>
                <Card size="small">
                  <Statistic
                    title="超阈值文档"
                    value={staleItems.length}
                    valueStyle={{ color: staleItems.length ? '#faad14' : undefined }}
                  />
                </Card>
              </Col>
              <Col span={6}>
                <Card size="small"><Statistic title="检索未命中" value={staleness.retrieval_miss_total || 0} /></Card>
              </Col>
            </Row>

            <Card size="small" title="按类别的新鲜度分布" style={{ marginBottom: 16 }}>
              {Object.keys(byCategory).length ? (
                <Table
                  size="small"
                  rowKey="category"
                  dataSource={Object.entries(byCategory).map(([category, v]) => ({ category, ...v }))}
                  columns={categoryColumns}
                  pagination={false}
                />
              ) : (
                <Text type="secondary">暂无启用中的 KB 文档</Text>
              )}
            </Card>

            <Card size="small" title={`陈旧文档清单（阈值 ${threshold ?? '-'} 天）`} style={{ marginBottom: 16 }}>
              <Table
                size="small"
                rowKey="doc_id"
                dataSource={staleItems}
                columns={staleColumns}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                locale={{ emptyText: '没有超过阈值的文档' }}
              />
            </Card>
          </>
        )}

        <Card
          size="small"
          title={<span><FileTextOutlined /> KB 文档</span>}
          extra={docs?.total != null ? <Text type="secondary">共 {docs.total} 篇（接口单次最多返回 200 篇）</Text> : null}
        >
          {items.length ? (
            <Table
              size="small"
              rowKey="doc_id"
              dataSource={items}
              columns={docColumns}
              pagination={{ pageSize: 10 }}
            />
          ) : (
            <Empty description="暂无 KB 文档" />
          )}
        </Card>
      </Spin>
    </div>
  )
}

export default OamKb
