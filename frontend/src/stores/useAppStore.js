import { create } from 'zustand';

/**
 * 全局应用状态管理
 * 管理导航、数据库列表、告警、健康评分、连接状态等共享状态
 */
const useAppStore = create((set, get) => ({
  // --- 导航树状态 ---
  collapsed: false,
  toggleCollapsed: () => set((state) => ({ collapsed: !state.collapsed })),
  setCollapsed: (collapsed) => set({ collapsed }),

  // --- 当前选中的数据库 ---
  selectedDbId: null,
  selectedDbName: '',
  selectedDbType: '',
  setSelectedDb: (id, name, type) =>
    set({ selectedDbId: id, selectedDbName: name, selectedDbType: type }),
  clearSelectedDb: () =>
    set({ selectedDbId: null, selectedDbName: '', selectedDbType: '' }),

  // --- 数据库列表缓存 ---
  databases: [],
  databasesLoading: false,
  databasesError: null,
  databasesLastFetched: null,
  setDatabases: (dbs) => set({
    databases: dbs,
    databasesLoading: false,
    databasesError: null,
    databasesLastFetched: Date.now(),
  }),
  setDatabasesLoading: (loading) => set({ databasesLoading: loading }),
  setDatabasesError: (error) => set({ databasesError: error, databasesLoading: false }),
  getDatabaseById: (id) => {
    const state = get();
    return state.databases.find((db) => db.id === id);
  },

  // --- 导航树数据（按 DB 类型分组） ---
  navTree: [],
  navTreeLoading: false,
  setNavTree: (tree) => set({ navTree: tree }),
  setNavTreeLoading: (loading) => set({ navTreeLoading: loading }),

  // --- 搜索 ---
  globalSearchKeyword: '',
  setGlobalSearchKeyword: (keyword) => set({ globalSearchKeyword: keyword }),

  // --- 告警统计 ---
  alertCounts: { warning: 0, error: 0, critical: 0 },
  alerts: [],
  alertsLoading: false,
  setAlertCounts: (counts) => set({ alertCounts: counts }),
  setAlerts: (alerts) => set({ alerts }),
  setAlertsLoading: (loading) => set({ alertsLoading: loading }),

  // --- 健康分数缓存 ---
  healthScores: {},
  setHealthScore: (dbId, score) =>
    set((state) => ({
      healthScores: { ...state.healthScores, [dbId]: score },
    })),
  setHealthScores: (scores) => set({ healthScores: scores }),

  // --- 数据库状态缓存 (dbId -> status data) ---
  dbStatuses: {},
  setDbStatus: (dbId, statusData) =>
    set((state) => ({
      dbStatuses: { ...state.dbStatuses, [dbId]: statusData },
    })),
  setDbStatuses: (statuses) => set({ dbStatuses: statuses }),
  clearDbStatuses: () => set({ dbStatuses: {} }),

  // --- 平台健康状态 ---
  platformHealth: null,
  setPlatformHealth: (health) => set({ platformHealth: health }),

  // --- 趋势数据缓存 (dbId -> trendData) ---
  trendDataCache: {},
  setTrendData: (dbId, data) =>
    set((state) => ({
      trendDataCache: { ...state.trendDataCache, [dbId]: data },
    })),

  // --- SSE 实时连接状态 ---
  sseConnected: false,
  sseConnection: null,
  _sseReconnectAttempts: 0,
  setSseConnected: (connected) => set({ sseConnected: connected }),

  connectSSE: () => {
    const state = get();
    if (state.sseConnection) return; // 已连接

    const token = localStorage.getItem('auth_token');
    const baseApi = import.meta.env.VITE_API_BASE || '/api/v1';
    const url = token
      ? `${baseApi}/events/?token=${token}`
      : `${baseApi}/events/`;

    const es = new EventSource(url);

    es.addEventListener('connected', () => {
      set({ sseConnected: true, _sseReconnectAttempts: 0 });
    });

    es.addEventListener('alert', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.action === 'fire') {
          // 更新告警计数
          const counts = { ...get().alertCounts };
          if (data.severity === 'critical') counts.critical += 1;
          else if (data.severity === 'error') counts.error += 1;
          else counts.warning += 1;
          set({ alertCounts: counts });
        } else if (data.action === 'resolve') {
          // 减少活跃告警
          const counts = { ...get().alertCounts };
          counts.critical = Math.max(0, counts.critical - 1);
          set({ alertCounts: counts });
        }
      } catch (_) {}
    });

    es.addEventListener('metric', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.config_id && data.metrics) {
          set((state) => ({
            dbStatuses: {
              ...state.dbStatuses,
              [data.config_id]: {
                ...state.dbStatuses[data.config_id],
                ...data.metrics,
                _updatedAt: Date.now(),
              },
            },
          }));
        }
      } catch (_) {}
    });

    es.addEventListener('heartbeat', () => {
      // 心跳正常，无需处理
    });

    es.onerror = () => {
      set({ sseConnected: false, sseConnection: null });
      es.close();
      // 指数退避重连，最多5次后停止
      const attempts = get()._sseReconnectAttempts;
      if (attempts >= 5) {
        console.warn('[SSE] 重连次数已达上限，停止重连。实时推送不可用，请刷新页面重试。');
        return;
      }
      const delay = Math.min(1000 * Math.pow(2, attempts), 30000);
      set({ _sseReconnectAttempts: attempts + 1 });
      setTimeout(() => get().connectSSE(), delay);
    };

    set({ sseConnection: es });
  },

  disconnectSSE: () => {
    const state = get();
    if (state.sseConnection) {
      state.sseConnection.close();
      set({ sseConnection: null, sseConnected: false });
    }
  },
}));

export default useAppStore;
