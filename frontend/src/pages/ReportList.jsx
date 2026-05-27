import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Space, Tag, message, Modal, Select, Spin,
  Popconfirm, Tooltip, Empty, Descriptions, Row, Col, Statistic,
} from 'antd';
import {
  FileTextOutlined, DownloadOutlined, EyeOutlined,
  ReloadOutlined, CalendarOutlined, PlusOutlined,
} from '@ant-design/icons';
import { reportAPI } from '../services/api';
import { PermissionGuard } from '../components/AuthGuard';
import { Perm } from '../utils/permission';

const { Option } = Select;

export default function ReportList() {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewHtml, setPreviewHtml] = useState('');
  const [previewTitle, setPreviewTitle] = useState('');
  const [filterType, setFilterType] = useState(null);
  const [generateModal, setGenerateModal] = useState(false);
  const [generateLoading, setGenerateLoading] = useState(false);
  const [generateType, setGenerateType] = useState('daily');

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (filterType) params.report_type = filterType;
      const res = await reportAPI.list(params);
      const data = res?.data || res || {};
      setReports(data.reports || data.results || []);
    } catch (e) {
      message.error('加载报表列表失败');
    }
    setLoading(false);
  }, [filterType]);

  useEffect(() => { loadReports(); }, [loadReports]);

  const handlePreview = (record) => {
    if (record.content_html) {
      setPreviewTitle(record.title);
      setPreviewHtml(record.content_html);
      setPreviewVisible(true);
    } else {
      message.info('该报表暂无预览内容');
    }
  };

  const handleDownload = async (record) => {
    try {
      const res = await reportAPI.download(record.id);
      const url = window.URL.createObjectURL(new Blob([res], { type: 'text/html' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `${record.title || 'report'}.html`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      message.success('下载成功');
    } catch (e) {
      message.error('下载失败');
    }
  };

  const handleGenerate = async () => {
    try {
      setGenerateLoading(true);
      await reportAPI.generate({ report_type: generateType });
      message.success('报表生成成功');
      setGenerateModal(false);
      loadReports();
    } catch (e) {
      message.error('报表生成失败: ' + (e.response?.data?.error || e.message));
    } finally {
      setGenerateLoading(false);
    }
  };

  const typeColors = { daily: 'blue', weekly: 'green', monthly: 'orange' };
  const typeLabels = { daily: '日报', weekly: '周报', monthly: '月报' };
  const statusColors = { generated: 'blue', sent: 'green', failed: 'red' };
  const statusLabels = { generated: '已生成', sent: '已发送', failed: '发送失败' };

  const dailyCount = reports.filter(r => r.report_type === 'daily').length;
  const weeklyCount = reports.filter(r => r.report_type === 'weekly').length;
  const monthlyCount = reports.filter(r => r.report_type === 'monthly').length;

  const columns = [
    {
      title: '报表标题', dataIndex: 'title', key: 'title', width: 240,
      render: (v, r) => (
        <Button type="link" size="small" onClick={() => handlePreview(r)} style={{ padding: 0 }}>
          {v}
        </Button>
      ),
    },
    {
      title: '类型', dataIndex: 'report_type', key: 'report_type', width: 80,
      render: v => <Tag color={typeColors[v]}>{typeLabels[v] || v}</Tag>,
    },
    {
      title: '统计周期', key: 'period', width: 180,
      render: (_, r) => `${r.period_start} ~ ${r.period_end}`,
    },
    {
      title: '收件人', dataIndex: 'recipients', key: 'recipients', width: 200,
      render: v => v?.length ? v.join(', ') : '-',
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: v => <Tag color={statusColors[v]}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '生成时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      sorter: (a, b) => new Date(a.created_at) - new Date(b.created_at),
      defaultSortOrder: 'descend',
      render: v => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_, r) => (
        <Space>
          <Tooltip title="预览">
            <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => handlePreview(r)} />
          </Tooltip>
          <Tooltip title="下载">
            <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(r)} />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={6}>
          <Card><Statistic title="日报" value={dailyCount} prefix={<CalendarOutlined />} valueStyle={{ color: '#1890ff' }} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="周报" value={weeklyCount} prefix={<CalendarOutlined />} valueStyle={{ color: '#52c41a' }} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="月报" value={monthlyCount} prefix={<CalendarOutlined />} valueStyle={{ color: '#fa8c16' }} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="报表总数" value={reports.length} prefix={<FileTextOutlined />} /></Card>
        </Col>
      </Row>

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space>
          <FileTextOutlined />
          <span>报表中心 — 查看和下载系统自动生成的巡检报表</span>
          <Select
            style={{ width: 120 }} allowClear placeholder="报表类型"
            value={filterType} onChange={v => setFilterType(v)}
          >
            <Option value="daily">日报</Option>
            <Option value="weekly">周报</Option>
            <Option value="monthly">月报</Option>
          </Select>
          <Button size="small" icon={<ReloadOutlined />} onClick={loadReports}>刷新</Button>
          <PermissionGuard code={Perm.REPORTS_GENERATE}><Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setGenerateModal(true)}>生成报表</Button></PermissionGuard>
        </Space>
      </Card>

      <Table
        dataSource={reports} columns={columns} rowKey="id"
        loading={loading} size="small"
        pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }}
        locale={{ emptyText: <Empty description="暂无报表，点击「生成报表」创建" /> }}
      />

      <Modal
        title={previewTitle}
        open={previewVisible}
        onCancel={() => setPreviewVisible(false)}
        width={900}
        footer={null}
        styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}
      >
        <div dangerouslySetInnerHTML={{ __html: previewHtml }} />
      </Modal>

      <Modal
        title="生成报表"
        open={generateModal}
        onOk={handleGenerate}
        onCancel={() => setGenerateModal(false)}
        confirmLoading={generateLoading}
        okText="开始生成"
      >
        <div style={{ margin: '16px 0' }}>
          <p>选择要生成的报表类型：</p>
          <Select
            style={{ width: '100%' }}
            value={generateType}
            onChange={setGenerateType}
          >
            <Option value="daily">日报 - 最近一天的巡检数据</Option>
            <Option value="weekly">周报 - 最近一周的巡检数据</Option>
            <Option value="monthly">月报 - 最近一个月的巡检数据</Option>
          </Select>
        </div>
      </Modal>
    </div>
  );
}
