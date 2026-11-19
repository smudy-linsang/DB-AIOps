# DB-029：修复 DB-027 独立 UAT 缺陷 D-01～D-07

执行：O

## 背景

- DB-027 独立 UAT（MMMan，报告 `docs/pm/reports/DB-027-report-M-team.md` v1.0）对 O 的三批体验改进（`ab79a04`、`4e607c7`、`0e06290`、`c911170`）做真浏览器独立验收：47 条流程（45 通过 / 1 失败 / 1 未复现），缺陷 7 条（中 2 / 低 5），结论有条件通过。
- PM 已在当前 main 上独立核对代码，确认 D-01、D-02 属实；其余 5 条低缺陷以报告为准。
- PM 裁决 DB-027 有条件通过，条件即本单；修好并复验通过后 DB-027 转通过。

## 任务

1. **D-01告警中心「详情」按钮点击必抛未捕获异常**：`frontend/src/pages/AlertList.jsx:149` 列 render 引用作用域外的 `onView`（真实 handler 在同文件以 `tableProps.onView = setDetailAlert` 传入），点击抛 `ReferenceError: onView is not defined`，详情抽屉打不开，恢复依据在告警中心无法查看。修复列渲染的作用域问题（handler 经 tableProps 透传到列 render，或列定义改为组件内生成）；补一条前端回归用例覆盖「点击详情打开抽屉」。
2. **D-02实时推送降级页面无可见提示**：`frontend/src/stores/useAppStore.js:92-160` 只监听 `connected` / `alert` / `metric` / `heartbeat`，未监听后端 `degraded` 事件；`USE_REDIS_CACHE=False` 下后端立即返回 `event: degraded` 并关闭连接，前端指数退避重连 5 次后只在控制台打印、页面无任何提示。修复：监听 `degraded` 事件并直接呈现降级提示（复用既有「系统降级」入口），不再做无意义的 5 次退避重连。
3. **D-03～D-07同批清理**：事故详情页 DOM 嵌套非法（`IncidentDetail.jsx:96`，Spin 在 p 内）；纳管弹窗 useForm 未挂载到 Form（`DatabaseList.jsx`）；antd 弃用告警 4 类（Card bordered、Modal destroyOnClose、Modal 静态函数、Tabs.TabPane）；表单字段缺 id/name 与 autocomplete；实例列表「最后采集」列英文相对时间改中文。
4. **复验**：真浏览器复跑 D-01（点详情 → 抽屉打开、恢复依据可见）与 D-02（降级 → 页面可见提示、无多余重连）两条流程；跑 `scripts/validate.sh unit` 与前端门禁；直推后复核 GitHub CI 全绿。

## 验收物

- 报告 `docs/pm/reports/DB-029-report-O.md`（第一行结论三档之一：通过/有条件通过/不通过），写清每条缺陷的修复内容、复验结果（D-01/D-02 附浏览器实测）、本地校验与 CI 结果。

## 约束

- 只修本单 7 条缺陷，不得顺手改其他功能与既有测试断言。
- 不改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`（仅部门经理维护）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-19 | muse | DB-029 下达：修复 DB-027 独立 UAT 缺陷 D-01～D-07，派 O（PM 裁决 DB-027 有条件通过的条件） |
