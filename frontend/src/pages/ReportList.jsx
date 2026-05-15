/**
 * ReportList - 报表管理页面 (Phase 4)
 *
 * 功能：
 * - 查看已生成的报表列表（日报/周报/月报）
 * - 预览/下载 HTML 报表
 * - 手动触发报表生成
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Space, Tag, message, Modal, Select, Spin,
  Popconfirm, Tooltip, Empty, Descriptions, Input,
} from 'antd';
import {
  FileTextOutlined, DownloadOutlined, EyeOutlined,
  ReloadOutlined, CalendarOutlined,
} from '@ant-design/icons';
import { reportAPI } from '../services/api';

const { Option } = Select;

export default function ReportList() {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewHtml, setPreviewHtml] = useState('');
  const [previewTitle, setPreviewTitle] = useState('');
  const [filterType, setFilterType] = useState(null);

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (filterType) params.report_type = filterType;
      const res = await reportAPI.list(params);
      const data = res?.data || res || {};
      setReports(data.results || data || []);
    } catch (e) {
      message.error('加载报表列表失败');
    }
    setLoading(false);
  }, [filterType]);

  useEffect(() => { loadReports(); }, [loadReports]);

  const handlePreview = async (record) => {
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
      // 创建下载链接
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

  const typeColors = { daily: 'blue', weekly: 'green', monthly: 'orange' };
  const typeLabels = { daily: '日报', weekly: '周报', monthly: '月报' };
  const statusColors = { generated: 'blue', sent: 'green', failed: 'red' };
  const statusLabels = { generated: '已生成', sent: '已发送', failed: '发送失败' };

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
        </Space>
      </Card>

      <Table
        dataSource={reports} columns={columns} rowKey="id"
        loading={loading} size="small"
        pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }}
        locale={{ emptyText: <Empty description="暂无报表，可通过 manage.py generate_report 命令生成" /> }}
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
    </div>
  );
}
