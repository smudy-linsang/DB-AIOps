# DB-023 补充 · 实库补证证据（GorkMan）

- 通道：DB-009 已验证通道（`DatabaseConfig` + `monitor.db_connector.get_connector`），未索取新凭据，未重启采集器。
- 达梦：平台配置 id=9，`db_type=dm`，名称 `DM_测试库`，端口已掩码为 `<dm-port>`；版本 `DM Database Server x64 V8`（`V$VERSION` / `V$INSTANCE.SVR_VERSION`），库名 `DAMENG`，`ID_CODE=--03134284488-20260330-320063-20200 Pack2`。
- GBase：平台配置 id=11，`db_type=gbase`，名称 `GBase8A_测试库`，端口 5258（任务单已写明）；`VERSION()=8.6.2.43-R7-free.110605`。同端口另有 id=15 `probe-gbase`，本单未用。
- 宿主机检出 HEAD：`1d8ebbf5f7ca4c005bfe962c6a3f4ceeca911c2a`（其父提交即本单回执 `584e9b11820ba1bc57b21ea602f2870950d43827`，检测器实现来自原单 tip `b3927d48`，本单未改检测器）。
- 掩码：主机名为 `<local-host>`，用户主目录为 `<user-home>`，达梦端口为 `<dm-port>`；口令从未落盘，仅记录 `password_len`。
- 主测时刻：2026-11-13T10:34:45+08:00（洛杉矶时间 2026-11-12 18:34:45 PST）。诊断 10:37+08，补充盘点 10:38+08。

## 文件

| 文件 | 说明 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| db023_live_results.json | 7 项官方 SQL + `Batch010bDetector` 判定 | 20095 | f309c482f17395c0e1aed4c650dc0909d6374e4a21d1e526bb86e018fcdba8c5 |
| db023_live_results.txt | 同上文本摘要 | 5023 | 73703c45fd215663d103e97f912ab736fd98af032ca5806389d855e03bfc27e1 |
| db023_gbase_diag.json | 五个库逐个 USE 后查目标视图；information_schema 近似名检索 | 7941 | 05a9cc37b239e4c014b038a484b4d810cbcf538b29db6fff3184ac7aaba94849 |
| db023_extra_inventory.json | 达梦版本；各库 SHOW TABLES；`gbase.table_distribution` 列与样例 | 12250 | 7c7dcb94ba0e7e0c050cc800466c76f9cd0cf000d897d790a9bab23d8ebc2375 |
| manifest.json | 机器可读清单 | — | — |
