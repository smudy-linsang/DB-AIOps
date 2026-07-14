# Phase 7 第三册：性能中心前端（五 Tab 照图施工）

> 工单 7C-01 ~ 7C-07。路由沿用 `/databases/:id/performance`，页面由
> `DatabasePerformanceHub.jsx` **重构**为 `PerformanceCenter.jsx` 五 Tab
> （URL 同步 `?tab=home|top|ash|sql|blocking`，可直链分享）。
> 技术栈: 现有 React18 + antd + ECharts。所有字段名以 20 册响应样例为准。

---

## 1. 壳与公共组件（7C-01）

```
frontend/src/pages/PerformanceCenter.jsx        # 壳: 实例头 + 时间选择器 + 五Tab
frontend/src/components/perf/
  ├── TimeRangeBar.jsx      # 窗口选择(30m/1h/6h/24h/7d/自定义) + 自动刷新开关(10s)
  ├── AasChart.jsx          # AAS 堆叠面积图(核心复用组件, 见 §2.2)
  ├── WaitClassTag.jsx      # 等待类色块标签(颜色契约=README §3, 唯一取色处)
  ├── SqlLink.jsx           # digest 短链 → SQL 详情抽屉
  ├── SessionDrawer.jsx     # 会话详情抽屉(样本历史+kill入口)
  └── waitClassMeta.js      # {key:{label,color}} 九类契约常量(与后端枚举逐字对齐)
```
- 实例头: 名称/类型/状态 + 卡片行(DB Time、平均 AAS、CPU 核数、Top 等待类)——数据
  取 `/perf/aas/` totals。
- 菜单: 侧栏「数据库管理」下已有详情页入口, 另在 `SQLMonitoring`(全局慢SQL页)每行
  加"进性能中心"链接。权限点 `perf:view` 注册进 PermissionRoute 表。

**验收 7C-01**: 五 Tab 可切换且 URL 同步; 无 perf:view 角色进入 403; 断网时全页
不白屏(每 Tab 独立 error boundary + degraded 提示条)。

---

## 2. Tab1 性能主页（7C-02）

布局（EMCC Performance Home 形制）:
```
┌ 卡片行: DB Time | 平均AAS | Max AAS | CPU核数 | Top等待类 ┐
├──────────────────────────────────────────────────────────┤
│  AAS 按等待类堆叠面积图 (高度 320, 含 Max CPU 红虚线)        │
│  [dataZoom 内置刷选]                                       │
├────────────────────────┬─────────────────────────────────┤
│ Top SQL (窗口内 Top10)  │ Top 会话 (窗口内 Top10)           │
│ 条形=按等待类分段堆叠     │ 条形=按等待类分段堆叠              │
└────────────────────────┴─────────────────────────────────┘
```

### 2.2 AasChart ECharts 契约（关键配置, 照抄）
```js
option = {
  color: seriesKeys.map(k => waitClassMeta[k].color),
  tooltip: { trigger: 'axis', formatter: aasTooltip /* 每类值+合计, 保留2位 */ },
  legend: { data: seriesKeys.map(k => waitClassMeta[k].label), bottom: 0 },
  xAxis: { type: 'time' },
  yAxis: { type: 'value', name: '平均活动会话数(AAS)', minInterval: 1 },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18 }],
  series: [
    ...seriesKeys.map(k => ({
      name: waitClassMeta[k].label, type: 'line', stack: 'aas',
      areaStyle: { opacity: 0.85 }, symbol: 'none', lineStyle: { width: 0 },
      data: series[k],   // [[ts, aas], ...]
    })),
    cpuCores != null && {
      name: 'Max CPU', type: 'line', symbol: 'none', silent: true, data: [],
      markLine: { symbol: 'none', lineStyle: { color: '#d40000', type: 'dashed' },
                  label: { formatter: `CPU 核数 ${cpuCores}` },
                  data: [{ yAxis: cpuCores }] },
    },
  ].filter(Boolean),
};
```
- **联动契约**: dataZoom 事件 debounce 400ms → 以缩放窗口调 `/perf/top-activity/`
  刷新下方双 Top（EMCC 灵魂交互）。
- Top 条形行内堆叠: antd Table + 自绘 flex 色段（宽度=各类 active_sec 占该行
  总量比), 悬浮显示明细; 行点击 → SqlLink 抽屉 / SessionDrawer。

**验收 7C-02**: 注入锁演练时主页 concurrency 暗红带明显隆起且 Top SQL 首行即注入
SQL; 缩放时间窗后双 Top 数据随窗口变化; cpu_cores 缺失不画线不报错。

---

## 3. Tab2 顶级活动（7C-03）

与 Tab1 区别（EMCC Top Activity 语义）: 大图占 60% 高度 + **brush 框选**
(`toolbox.brush lineX`)选中任意窗口, 右侧维度切换器
`SQL|会话|用户|模块|等待事件|对象`（对应 `/perf/top-activity/?dim=`）。
```
┌ AAS 大图 (brush 框选)                                      ┐
├──────────────┬───────────────────────────────────────────┤
│ 维度切换 Radio │ Top 表(选中窗口): 占比条 | key | 明细列      │
│              │ 行点击: dim=sql→SQL详情 / dim=session→会话   │
└──────────────┴───────────────────────────────────────────┘
```
brushEnd 事件 → 取 areas[0].coordRange 为 [from,to] → 双表刷新 + 顶部显示
"已选 10:02–10:17 (15min)" 可清除。

**验收 7C-03**: 框选窗口后 Top 表与全窗口明显不同(拿演练验证); 六个维度都能返回。

---

## 4. Tab3 ASH 分析（7C-04）

EMCC ASH Analytics 形制 = 过滤器叠加 + 维度分布 + 明细:
```
┌ 过滤器链: [wait_class=user_io ×] [user=app ×] (+ 添加过滤)  ┐
├ 时间线小图(过滤后 AAS)                                      ┤
├───────────────┬──────────────────────────────────────────┤
│ 维度面板(手风琴) │ 命中样本明细表(时间/会话/SQL/等待/对象, 分页) │
│ 9 维 Top15 条形 │ (raw 7d 内; 超期提示切聚合视图)             │
│ 条目点击=加过滤  │                                           │
└───────────────┴──────────────────────────────────────────┘
```
- 全部数据来自 `/perf/ash-facets/`; 过滤器状态即 `filters` 参数, 面板条目点击追加
  过滤（同列互斥替换）; URL 同步（可分享一个"切片现场"）。
- 明细表列: time/session_id/user/db/wait_class(Tag)/wait_event/sql(SqlLink)/
  lock_object/active_secs。

**验收 7C-04**: 叠 2 层过滤后时间线/面板/明细三区一致收敛; 过滤链 URL 直链可复现。

---

## 5. Tab4 SQL 监控（7C-05）

上半屏「运行中语句」（10s 自动刷新, `/perf/running-sql/`）:
列 = 会话 | SQL(SqlLink) | 用户 | 已运行(计时) | 等待类 | 阶段 phase |
进度(Progress 条, null→仅计时) | 预计剩余 | [查看计划] [申请终止]。

下半屏「SQL 详情」（点击任意 digest 打开, 也可从其他 Tab 进入; 组件
`SqlDetailPanel.jsx` 供抽屉复用）:
```
┌ 归一化文本 + 复制 + digest/db_type 徽标                      ┐
├ 四联趋势小图(执行次数/平均耗时/行数/逻辑读) + ASH 活动占比环图 ┤
├ Tabs: [执行计划] [计划对比] [优化建议] [关联事故]             ┤
│  执行计划: 计划列表(is_current 标星) + plan_text 等宽展示     │
│  计划对比: 左右两 plan_text diff 高亮(react-diff-viewer 或    │
│           自实现行级 diff), 顶部提示 plan_changed_at         │
│  优化建议: advisor.index_suggestions 卡片 + [手动EXPLAIN] 按钮│
│  关联事故: related_incidents 表 → 跳作战室                   │
└──────────────────────────────────────────────────────────┘
```

**验收 7C-05**: Oracle 建索引期间运行中列表出现进度条; 详情页对演练慢 SQL 四趋势
有数据; 计划对比能看出两 plan 差异行。

---

## 6. Tab5 阻塞分析（7C-06）

```
┌ 时间选择: (●实时 10s刷新 | ○历史回放 [时间选择器])            ┐
├ 阻塞树 antd Table(treeData, 默认全展开):                     │
│  列: 会话 | 用户 | 角色(根源/被阻塞 Tag) | 等待秒 | 锁类型/模式 │
│      | 争用对象 | 当前SQL | 影响面(subtree_waiters) | 操作     │
│  根源行整行淡红底; 操作=[申请终止](7B-07 审批链弹窗)           │
├ 底部: 该实例近 24h 阻塞热力时间条(blocked_sec/1m, 点击=回放)   │
└──────────────────────────────────────────────────────────┘
```
- 审批链弹窗: 第一步提交 reason → 显示 audit_id; 若当前用户有审批权限, 同弹窗
  出现"审批并执行"第二步（两步都留痕）; 完成后 3s 后自动刷新树。
- 历史回放 = `/perf/blocking-tree/?at=<ts>`; 热力条数据复用 session_ash_1m 的
  blocked_sec 序列（`/perf/aas/?by=wait_class` 的 concurrency 亦可, 施工用前者）。

**验收 7C-06**: 锁演练 3 层链在树中层级正确、根源标红、影响面=2; 走完审批链
kill 后树自动清空; 回放能看到 5 分钟前的链。

---

## 7. 期间对比抽屉 + 事故联动（7C-07）

- 全局按钮（壳右上）「期间对比」→ 抽屉: 两组时间选择(快捷: 今天vs昨天/
  本周vs上周/自定义) → `/perf/compare/` → 三区: AAS 双柱按类对比 |
  Top SQL diff 表(ratio 降序, new/gone 徽标) | Top 等待事件 diff 表。
- 事故联动: ① 作战室(IncidentDetail)证据区加"在性能中心查看"深链
  `?tab=ash&from=<事故窗口>&filters=...`; ② 性能中心各 Tab 检测到当前窗口内
  该实例有 open 事故时顶部横幅提示并链回作战室。

**验收 7C-07**: 演练前后窗口对比, 注入 SQL ratio 排第一且 new 徽标正确;
作战室深链打开 ASH Tab 且过滤器/窗口已就位。

---

## 8. 形似验收清单（对照 EMCC 逐要素, 7D-05 终验引用）

| EMCC 要素 | 本系统落点 | 判定 |
|---|---|---|
| AAS 堆叠面积 + 等待类标准配色 + Max CPU 线 | Tab1 AasChart | 逐项目视 |
| 图表刷选联动 Top SQL/Top 会话 | Tab1 dataZoom / Tab2 brush | 交互走查 |
| ASH 维度过滤器叠加 + 分布面板 | Tab3 | 交互走查 |
| 运行中 SQL + 进度 + 计划 | Tab4 上半屏 | 三库各一例 |
| SQL 详情: 趋势/计划/计划对比/建议 | Tab4 下半屏 | 走查 |
| 阻塞树 + 锁明细 + 会话终止 | Tab5 | 演练走查 |
| 期间对比 | 对比抽屉 | 走查 |
