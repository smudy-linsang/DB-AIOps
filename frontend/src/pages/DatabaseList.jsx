import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { 
  Table, Tag, Space, Button, Input, Select, Card, 
  Typography, Tooltip, Statistic, Row, Col, message
} from 'antd'
import { 
  SearchOutlined, ReloadOutlined, PlusOutlined,
  DatabaseOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined, WarningOutlined
} from '@ant-design/icons'
import { databaseAPI } from '../services/api'
import dayjs from 'dayjs'

const { Title, Text } = Typography
const { Option } = Select

const DatabaseList = () => {
  const [databases, setDatabases] = useState([])
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState({
    search: '',
    dbType: 'all',
    status: 'all'
  })

  const fetchDatabases = async () => {
    setLoading(true)
    try {
      const response = await databaseAPI.list()
      setDatabases(response?.databases || [])
      message.success('数据加载成功')
    } catch (error) {
      console.error('获取数据失败:', error)
      message.error('获取数据失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDatabases()
  }, [])

  const getStatusTag = (db) => {
    // 基于 is_active 和其他指标判断状态
    const isActive = db.is_active !== false
    if (!isActive) {
      return <Tag color="default" icon={<ClockCircleOutlined />}>离线</Tag>
    }
    // 可以基于最新指标判断
    return <Tag color="green" icon={<CheckCircleOutlined />}>正常</Tag>
  }

  const getDbTypeTag = (type) => {
    const typeMap = {
      oracle: { color: 'red', text: 'Oracle' },
      mysql: { color: 'blue', text: 'MySQL' },
      pgsql: { color: 'green', text: 'PostgreSQL' },
      dm: { color: 'purple', text: 'DM达梦' },
      gbase: { color: 'cyan', text: 'GBase' },
      tdsql: { color: 'orange', text: 'TDSQL' }
    }
    const config = typeMap[type?.toLowerCase()] || { color: 'default', text: type }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  const filteredDatabases = databases.filter(db => {
    if (filters.search && !db.name?.toLowerCase().includes(filters.search.toLowerCase())) {
      return false
    }
    if (filters.dbType !== 'all' && db.db_type !== filters.dbType) {
      return false
    }
    return true
  })

  const columns = [
    {
      title: '数据库名称',
      dataIndex: 'name',
      key: 'name',
      render: (text, record) => (
        <Space direction="vertical" size="small">
          <Link to={`/databases/${record.id}`}>
            <Text strong style={{ fontSize: 14 }}>{text}</Text>
          </Link>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.host}:{record.port}
          </Text>
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
      key: 'status',
      width: 100,
      render: (_, record) => getStatusTag(record)
    },
    {
      title: '环境',
      dataIndex: 'environment',
      key: 'environment',
      width: 100,
      render: (env) => env ? <Tag>{env}</Tag> : '-'
    },
    {
      title: '最后更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 150,
      render: (time) => time ? dayjs(time).format('MM-DD HH:mm') : '-'
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, record) => (
        <Space size="small">
          <Link to={`/databases/${record.id}`}>
            <Button type="link" size="small">详情</Button>
          </Link>
        </Space>
      )
    }
  ]

  return (
    <div className="database-list" style={{ padding: 0 }}>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ marginBottom: 16 }}>
          <DatabaseOutlined /> 数据库列表
        </Title>
        
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="总数据库数" 
                value={databases.length}
                prefix={<DatabaseOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="正常运行" 
                value={databases.filter(d => d.is_active !== false).length}
                valueStyle={{ color: '#52c41a' }}
                prefix={<CheckCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="离线" 
                value={databases.filter(d => d.is_active === false).length}
                valueStyle={{ color: '#999' }}
                prefix={<ClockCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic 
                title="总数据库类型" 
                value={new Set(databases.map(d => d.db_type)).size}
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
            <Option value="pgsql">PostgreSQL</Option>
            <Option value="dm">DM达梦</Option>
            <Option value="gbase">GBase</Option>
            <Option value="tdsql">TDSQL</Option>
          </Select>
          <Button 
            icon={<ReloadOutlined />} 
            onClick={fetchDatabases}
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
  )
}

export default DatabaseList
