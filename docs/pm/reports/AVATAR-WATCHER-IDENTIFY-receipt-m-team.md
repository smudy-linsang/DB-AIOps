# AVATAR-WATCHER-IDENTIFY 接单回执（MMMan / M-team）

**接单时间**：2026-11-11 19:48（自动叫醒 19:47:53 由 member_poller 派单，仓库 HEAD `9fde63c` 已 `git pull --ff-only` 同步）  
**预计交付**：2026-11-11 20:20（本报告出具前）  
**执行**：MMMan（M-team）  
**任务单**：`docs/pm/orders/AVATAR-WATCHER-IDENTIFY.md`（watcher 类临时单号，不占 DB 编号）

## 一、接单与范围确认

本单为**只读调查**：查明 ①头像同步单的派单通道、②watcher 本体（进程/服务/脚本/计划任务/路径/启动方式/修改时间）、③设立者可否判定、④头像状态判定依据。

**已遵守的红线**：全程只读——未停用任何服务、未改任何配置、未删任何文件、未重启宿主桌面端、未裸改 `state.json`；本单不涉及平台代码改动，故不触发 `scripts/validate.sh unit`（未编辑任何被测代码）。

## 二、开查后已锁定的关键事实（详见交付报告）

1. **派单通道已端到端查清**：`C:\dispatcher\mman_dbaiops_quiet_scan.py`（M-team 自建的「安静扫描」守护，PID 28148 体系）读取 `C:\dispatcher\avatar_watch_state.json` 的 `todo` 数组 → 经 Fire HTTP 出口 `POST http://127.0.0.1:<宿主分配端口>/fire` → `C:\dispatcher\mman_fire_service.py` 拉起 `mcode exec` 会话 → 任务文本以 `【头像状态同步】…` 形式直接进入 MMMan 会话，**不经 `docs/pm/orders/`、不经 PM**。
2. **watcher 本体已查清**：`C:\dispatcher\mman_avatar_watcher.py`（pythonw 常驻，PID 12728，2026-11-11 09:12:48 起），由 Windows 计划任务 `\MMMan-DBAIOps-AvatarWatcher`（每 5 分钟）调用 `C:\dispatcher\mman_avatar_watchdog.py` 守护拉起。
3. **设立者**：装置位于 `C:\dispatcher\`（M-team 调度器目录）、文件名与计划任务名均带 `mman_` 前缀、注册账户为本机用户（SID `S-1-5-21-…-1001`）⇒ 属 M-team 自建装置，非第三方系统服务；但「是哪一次会话/哪条指令建立」的精确归属线索有限，将按任务单要求列清线索与死角。
4. **判定依据**：watcher 读 `C:\Users\linsa\.minimax\v2\plugin-data\liveboards\minimax-bot\messages\*.json`（`status='streaming'` → work；最近 `status='done'` 且文本首部含交付报告标记 → report；超过保持窗口 → idle），**不读宿主内存**；因此宿主内存态回写不落盘时，watcher 仍会重复判定「需要切换」，这与本日重复下发 5 次直接相关。

## 三、后续

交付报告：`docs/pm/reports/AVATAR-WATCHER-IDENTIFY-report-m-team.md`（第一行给出结论三档之一）。

---

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-11 | MMMan | 接单回执 |