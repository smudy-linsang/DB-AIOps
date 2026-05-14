/**
 * TargetNavigationTree - Oracle EMCC 风格目标导航树
 *
 * 功能：
 *  - 按数据库类型分组（6 种类型 + 自定义分组）
 *  - 每节点显示状态图标（🟢正常 🟡警告 🔴严重 ⚫离线）
 *  - 支持折叠/展开
 *  - 右键菜单：详情、告警配置、Performance Hub
 *  - 搜索过滤
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Tree, Spin, Badge, Tag, Typography, Input, Space,
  Tooltip, Dropdown, message,
} from 'antd';
import {
  DatabaseOutlined, WarningOutlined, SearchOutlined,
  CaretDownOutlined, ReloadOutlined, SettingOutlined,
  DashboardOutlined, ThunderboltOutlined, AlertOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { databaseAPI, healthAPI } from '../services/api';
import useAppStore from '../stores/useAppStore';

const { Text } = Typography;
const { Search } = Input;

// 数据库类型配置
const DB_TYPE_CONFIG = {
  oracle: { label: 'Oracle', color: '#f5222d', icon: '🔴' },
  mysql: { label: 'MySQL', color: '#1890ff', icon: '🔵' },
  pgsql: { label: 'PostgreSQL', color: '#336791', icon: '🐘' },
  dm: { label: '达梦 DM8', color: '#ee2222', icon: '🟤' },
  gbase: { label: 'GBase 8a', color: '#00a854', icon: '🟢' },
  tdsql: { label: 'TDSQL', color: '#108ee9', icon: '🟦' },
};

// 健康状态图标映射
const STATUS_ICON = {
  A: '🟢', B: '🟢', C: '🟡', D: '🟡', E: '🔴', F: '⚫',
};

const TargetNavigationTree = () => {
  const navigate = useNavigate();
  const {
    selectedDbId, selectedDbType, setSelectedDb,
    databases, setDatabases, navTree, setNavTree,
    navTreeLoading, setNavTreeLoading, healthScores, setHealthScores,
    globalSearchKeyword,
  } = useAppStore();

  const [searchText, setSearchText] = useState('');
  const [expandedKeys, setExpandedKeys] = useState([]);

  // 加载数据库列表
  const loadDatabases = useCallback(async () => {
    setNavTreeLoading(true);
    try {
      const res = await databaseAPI.list();
      const dbs = (res.databases || res.data || []).map((db) => ({
        ...db,
        db_type: db.db_type || db.type,
      }));
      setDatabases(dbs);

      // 并行加载健康分数
      const scores = {};
      await Promise.all(
        dbs.map(async (db) => {
          try {
            const hRes = await healthAPI.get(db.id);
            const hData = hRes.data || hRes || {};
            scores[db.id] = {
              grade: hData.overall_grade || hData.grade || 'C',
              score: hData.overall_score || hData.score || 70,
            };
          } catch (_) {
            scores[db.id] = { grade: 'C', score: null };
          }
        })
      );
      setHealthScores(scores);
    } catch (e) {
      message.error('加载数据库列表失败');
    } finally {
      setNavTreeLoading(false);
    }
  }, [setDatabases, setHealthScores, setNavTreeLoading, setNavTree]);

  useEffect(() => {
    loadDatabases();
    const timer = setInterval(loadDatabases, 60000);
    return () => clearInterval(timer);
  }, [loadDatabases]);

  // 构建导航树数据
  const treeData = useMemo(() => {
    if (!databases || databases.length === 0) return [];

    // 按数据库类型分组
    const grouped = {};
    databases.forEach((db) => {
      const type = (db.db_type || 'unknown').toLowerCase();
      if (!grouped[type]) grouped[type] = [];
      grouped[type].push(db);
    });

    const filteredSearch = searchText || globalSearchKeyword;

    return Object.entries(grouped).map(([type, dbs]) => {
      const config = DB_TYPE_CONFIG[type] || { label: type.toUpperCase(), color: '#666', icon: '📦' };
      const filteredDbs = filteredSearch
        ? dbs.filter((db) =>
            db.name?.toLowerCase().includes(filteredSearch.toLowerCase()) ||
            db.host?.toLowerCase().includes(filteredSearch.toLowerCase())
          )
        : dbs;

      if (filteredSearch && filteredDbs.length === 0) return null;

      const typeKey = `type-${type}`;

      return {
        key: typeKey,
        title: (
          <Space size={4}>
            <span>{config.icon}</span>
            <Text strong style={{ fontSize: 13, color: config.color }}>
              {config.label}
            </Text>
            <Tag style={{ marginLeft: 4, fontSize: 10, lineHeight: '16px' }}>
              {dbs.length}
            </Tag>
          </Space>
        ),
        icon: <DatabaseOutlined style={{ color: config.color }} />,
        selectable: false,
        children: filteredDbs.map((db) => {
          const health = healthScores[db.id];
          const grade = health?.grade || 'C';
          const isSelected = selectedDbId === db.id;
          const statusIcon = STATUS_ICON[grade] || '⚫';

          return {
            key: `db-${db.id}`,
            title: (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <span style={{ fontSize: 12, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {statusIcon} {db.name || db.host}
                </span>
                {grade && (
                  <Tag
                    color={grade === 'A' || grade === 'B' ? 'success' : grade === 'C' || grade === 'D' ? 'warning' : 'error'}
                    style={{ fontSize: 10, marginLeft: 4 }}
                  >
                    {grade}
                  </Tag>
                )}
              </div>
            ),
            isLeaf: true,
            data: db,
          };
        }),
      };
    }).filter(Boolean);
  }, [databases, healthScores, searchText, globalSearchKeyword, selectedDbId]);

  // 点击节点
  const handleSelect = (keys, info) => {
    const node = info.node;
    if (node.data) {
      const db = node.data;
      setSelectedDb(db.id, db.name, db.db_type);
      navigate(`/databases/${db.id}`);
    }
  };

  // 右键菜单
  const handleRightClick = ({ event, node }) => {
    event.preventDefault();
    // 右键菜单通过 dropdown 在节点上实现
  };

  const getContextMenuItems = (db) => [
    {
      key: 'detail',
      label: '查看详情',
      icon: <DashboardOutlined />,
      onClick: () => {
        setSelectedDb(db.id, db.name, db.db_type);
        navigate(`/databases/${db.id}`);
      },
    },
    {
      key: 'performance',
      label: 'Performance Hub',
      icon: <ThunderboltOutlined />,
      onClick: () => {
        setSelectedDb(db.id, db.name, db.db_type);
        navigate(`/databases/${db.id}/performance`);
      },
    },
    {
      key: 'alert-config',
      label: '告警配置',
      icon: <AlertOutlined />,
      onClick: () => {
        setSelectedDb(db.id, db.name, db.db_type);
        navigate('/alert-config');
      },
    },
    { type: 'divider' },
    {
      key: 'sql-monitoring',
      label: 'SQL 监控',
      icon: <SearchOutlined />,
      onClick: () => {
        setSelectedDb(db.id, db.name, db.db_type);
        navigate(`/sql-monitoring?db=${db.id}`);
      },
    },
  ];

  return (
    <div style={{ padding: '4px 0' }}>
      <div style={{ padding: '0 12px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 8 }}>
          <Input
            size="small"
            prefix={<SearchOutlined style={{ color: '#999' }} />}
            placeholder="搜索..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            allowClear
            style={{ borderRadius: 4 }}
          />
          <Tooltip title="刷新">
            <ReloadOutlined
              onClick={loadDatabases}
              style={{ cursor: 'pointer', color: '#666', fontSize: 14 }}
              spin={navTreeLoading}
            />
          </Tooltip>
        </div>
      </div>

      {navTreeLoading && databases.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="small" />
          <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>加载中...</div>
        </div>
      ) : treeData.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999', fontSize: 12 }}>
          暂无数据库
          <br />
          <a onClick={() => navigate('/databases')}>添加数据库</a>
        </div>
      ) : (
        <Tree.DirectoryTree
          showIcon
          defaultExpandAll={false}
          expandedKeys={expandedKeys}
          onExpand={(keys) => setExpandedKeys(keys)}
          onSelect={handleSelect}
          treeData={treeData}
          style={{ fontSize: 12 }}
          titleRender={(node) => {
            if (node.data) {
              return (
                <Dropdown
                  menu={{ items: getContextMenuItems(node.data) }}
                  trigger={['contextMenu']}
                >
                  <div style={{ width: '100%' }}>{node.title}</div>
                </Dropdown>
              );
            }
            return node.title;
          }}
        />
      )}
    </div>
  );
};

export default TargetNavigationTree;
