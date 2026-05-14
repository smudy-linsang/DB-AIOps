import { create } from 'zustand';

/**
 * 全局应用状态管理
 * 管理左侧导航折叠、选中的数据库、当前数据库类型等
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
  setDatabases: (dbs) => set({ databases: dbs }),
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
  setAlertCounts: (counts) => set({ alertCounts: counts }),

  // --- 健康分数缓存 ---
  healthScores: {},
  setHealthScore: (dbId, score) =>
    set((state) => ({
      healthScores: { ...state.healthScores, [dbId]: score },
    })),
  setHealthScores: (scores) => set({ healthScores: scores }),
}));

export default useAppStore;
