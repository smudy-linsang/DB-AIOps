import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { 
  Table, Tag, Space, Button, Input, Select, Card, 
  Typography, Tooltip, Badge, Statistic, Row, Col 
} from 'antd';
import { 
  SearchOutlined, ReloadOutlined, PlusOutlined,
  DatabaseOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined, WarningOutlined
} from '@ant-design/icons';
import { getDatabases, getDatabaseStats } from '../services/api';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { Option } = Select;

const DatabaseList = () => {
  const [databases, setDatabases] = useState([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState({
    total: 0,
    active: 0,
    error: 0,
    warning: 0
  });
  const [filters, setFilters] = useState({
    search: '',
    dbType: 'all',
    status: 'all'
  });

  const fetchData = async () => {
    setLoading(true);
    try {
      const [dbData, statsData] = await Promise.all([
        getDatabases(),
        getDatabaseStats()
      ]);
      setDatabases(dbData);
      setStats(statsData);
    } catch (error) {
      console.error('获取数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const getStatusTag = (status) => {
    const statusMap = {
      active: { color: 'green', text: '正常', icon: <CheckCircleOutlined /> },
      error: { color: 'red', text: '异常', icon: <CloseCircleOutlined /> },
      warning: { color: 'orange', text: '警告', icon: <WarningOutlined /> },
      inactive: { color: 'default', text: '离线', icon: <ClockCircleOutlined /> }
    };
    const config = statusMap[status] || statusMap.inactive;
    return (
      <Tag color={config.color} icon={config.icon}>
        {config.text}
      </Tag>
    );
  };

  const getDbTypeTag = (type) => {
    const typeMap = {
      oracle: { color: 'red', text: 'Oracle' },
      mysql: { color: 'blue', text: 'MySQL' },
      postgresql: { color: 'green', text: 'PostgreSQL' },
      dm: { color: 'purple', text: 'DM达梦' },
      gbase: { color: 'cyan', text: 'GBase' },
      tdsql: { color: 'orange', text: 'TDSQL' }
    };
    const config = typeMap[type] || { color: 'default', text: type };
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  const filteredDatabases = databases.filter(db => {
    if (filters.search && !db.name.toLowerCase().includes(filters.search.toLowerCase())) {
      return false;
    }
    if (filters.dbType !== 'all' && db.db_type !== filters.dbType) {
      return false;
    }
    if (filters.status !== 'all' && db.status !== filters.status) {
      return false;
    }
    return true;
  });

  const columns = [
    {
      title: '数据库名称',
      dataIndex: 'name',
      key: 'name',
      render: (text, record) => (
        <Space direction="vertical" size="small">
          <Link to={`/database/${record.id}`}>
            <Text strong style={{ fontSize: 14 }}>{text}</Text>
          </Link>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.host}:{record.port}</Text>
        </Space>
      )
    },
    {
      title: '类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 120,
      render: (type) => getDbTypeTag(type)
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status) => getStatusTag(status)
    },
    {
      title: 'CPU使用率',
      dataIndex: 'cpu_usage',
      key: 'cpu_usage',
      width: 120,
      render: (value) => {
        let color = 'green';
        if (value > 80) color = 'red';
        else if (value > 60) color = 'orange';
        return <ProgressBar value={value} color={color} />;
      }
    },
    {
      title: '内存使用率',
      dataIndex: 'memory_usage',
      key: 'memory_usage',
      width: 120,
      render: (value) => {
        let color = 'green';
        if (value > 85) color = 'red';
        else if (value > 70) color = 'orange';
        return <ProgressBar value={value} color={color} />;
      }
    },
    {
      title: '磁盘使用率',
      dataIndex: 'disk_usage',
      key: 'disk_usage',
      width: 120,
      render: (value) => {
        let color = 'green';
        if (value > 90) color = 'red';
        else if (value > 75) color = 'orange';
        return <ProgressBar value={value} color={color} />;
      }
    },
    {
      title: '活跃会话',
      dataIndex: 'active_sessions',
      key: 'active_sessions',
      width: 100,
      render: (value) => <Text>{value || 0}</Text>
    },
    {
      title: '最后检查',
      dataIndex: 'last_check',
      key: 'last_check',
      width: 150,
      render: (time) => time ? dayjs(time).format('MM-DD HH:mm:ss') : '-'
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="查看详情">
            <Link to={`/database/${record.id}`}>
              <Button type="link" size="small">详情</Button>
            </Link>
          </Tooltip>
          <Tooltip title="实时监控">
            <Link to={`/database/${record.id}/monitor`}>
              <Button type="link" size="small">监控</Button>
            </Link>
          </Tooltip>
        </Space>
      )
    }
  ];

  return (
    <div className="database-list" style={{ padding: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ marginBottom: 16 }}>
          <DatabaseOutlined /> 数据库列表
        </Title>
        
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="总数据库数" 
                value={stats.total}
                prefix={<DatabaseOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="正常运行" 
                value={stats.active}
                valueStyle={{ color: '#52c41a' }}
                prefix={<CheckCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="异常" 
                value={stats.error}
                valueStyle={{ color: '#ff4d4f' }}
                prefix={<CloseCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="警告" 
                value={stats.warning}
                valueStyle={{ color: '#faad14' }}
                prefix={<WarningOutlined />}
              />
            </Card>
          </Col>
        </Row>

        <Space style={{ marginBottom: 16 }} wrap>
          <Input
            placeholder="搜索数据库名称"
            prefix={<SearchOutlined />}
            style={{ width: 200 }}
            onChange={(e) => setFilters({...filters, search: e.target.value})}
            allowClear
          />
          <Select
            value={filters.dbType}
            onChange={(value) => setFilters({...filters, dbType: value})}
            style={{ width: 120 }}
          >
            <Option value="all">全部类型</Option>
            <Option value="oracle">Oracle</Option>
            <Option value="mysql">MySQL</Option>
            <Option value="postgresql">PostgreSQL</Option>
            <Option value="dm">DM达梦</Option>
            <Option value="gbase">GBase</Option>
            <Option value="tdsql">TDSQL</Option>
          </Select>
          <Select
            value={filters.status}
            onChange={(value) => setFilters({...filters, status: value})}
            style={{ width: 100 }}
          >
            <Option value="all">全部状态</Option>
            <Option value="active">正常</Option>
            <Option value="warning">警告</Option>
            <Option value="error">异常</Option>
            <Option value="inactive">离线</Option>
          </Select>
          <Button 
            icon={<ReloadOutlined />} 
            onClick={fetchData}
            loading={loading}
          >
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />}>
            添加数据库
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={filteredDatabases}
        rowKey="id"
        loading={loading}
        pagination={{
          defaultPageSize: 10,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`
        }}
      />
    </div>
  );
};

// 简单的进度条组件
const ProgressBar = ({ value, color }) => {
  const style = {
    width: '100%',
    height: 8,
    backgroundColor: '#f0f0f0',
    borderRadius: 4,
    overflow: 'hidden'
  };
  const fillStyle = {
    width: `${value || 0}%`,
    height: '100%',
    backgroundColor: color === 'red' ? '#ff4d4f' : color === 'orange' ? '#faad14' : '#52c41a',
    transition: 'width 0.3s'
  };
  return (
    <div style={style}>
      <div style={fillStyle} />
    </div>
  );
};

export default DatabaseList;
