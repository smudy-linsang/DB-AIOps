有条件通过

> 2026-11-05 续修：负责人列表格式已修复，线上扫描通过；Actions 实跑确认缺少两项仓库授权，尚未恢复执行。最新状态与证据见 [O 自动链修复记录](DB-003-O-自动链修复记录-20261105.md)，下文原始“默认分支 master / 授权未核实”属于历史记录。

# DB-003：O 侧 DB-AIOps 自动监听交付

- 执行人：O；2026-11-04（Asia/Shanghai）。此前“配置入口未知”的阻塞已解除，按用户提供的位置读取 Xinchuang main `84a40d93e663a1844fb3500eaf921efc41b54a88` 及 `WAKE-AUTORUN-report-O.md`。
- 本结论仅限 **O 侧代码与本地验证**，不是 DB-003 整单验收。正式汇总报告仍由 GorkMan 负责 `DB-003-report-自动链接入.md`。
- 接单与 ETA 已先行落库，见 `DB-003-ack-O.md`。DB-002 不占用；DB-001、STATUS、DECISIONS 未修改。

## 实现位置与行为

| 位置 | 交付 |
|---|---|
| `.github/workflows/o-autorun.yml` | DB-AIOps 自有 Actions 工作流，默认 runner，main 上 orders/reports/dispatch 推送触发，声明每 5 分钟巡检与手动入口；队列并发组独立 |
| `tools/o_db_autorun.py` | DB 编号与 O 精确路由、历史单排除、稳定身份去重、明确读取失败、模型启动前独立 ETA 回执 |
| `tools/o-autorun/engine/` | 固定复用既有协调器/执行器/加密状态及白名单发布能力，不依赖运行时拉取 Xinchuang 私仓 |
| `tools/o-autorun/provenance.json` | 上游提交和每个源文件/交付文件 SHA256；两处原异常分支为满足 DB-AIOps 红线作等价替换 |
| `tools/o-autorun/README.md` | 部署、轮次/前置凭证、默认分支限制、账号所有者配置方法及验收要求 |
| `tools/o-autorun/tests/test_db_board.py` | 9 项适配及真实本地 Git 链路测试 |

不能通过某仓 workflow 的 paths 监听另一仓，因此为 DB-AIOps 独立部署，保持 Xinchuang **源码、工作流、队列和调度零改动**。状态分支属于 DB-AIOps，不共享 Xinchuang checkpoint；写入只使用 DB-AIOps 自身短期 github.token，无跨仓 PAT。

自动收取 DB-004 起及 DB-TEST 临时单；DB-001..003 已人工承接，不追溯重复启动。显式 dispatch 可处理历史任务，并保留 source/round/requires 校验，等待前置凭证时不会抢跑。没有显式 dispatch 时 source_sha 固定到订单最后修改提交；普通报告更新不改变 job_key。改旧单需要新轮次时应显式 dispatch，不能覆盖旧报告。

正式执行前先独立推送含身份键、接单时间、3 小时 ETA 的回执；失败则不继续启动模型。报告与 receipt 仍经原引擎验证发布。读取失败令工作流报错，不按空黑板吞掉。

## 已实测

1. **适配 9/9**：O/OO/多人路由、历史排除、临时编号、重复单拒绝、缺执行行报错、读取失败、显式前置凭证、报告变更去重与回执幂等。真实临时 Git 裸仓实测发单 → ETA 回执 → **合成执行器** → 报告/receipt 校验推送 → 重扫不重复启动。不是模型实跑或线上广播验收。
2. **复用引擎 92/92**：运行于本次交付引擎的上游 coordinator/worker/diagnostics/dispatch 专项，覆盖加密 checkpoint、去重、故障恢复、白名单发布等。首次尝试上游 94 项时，2 项本机 installer/便携 GG/A helper 因不在此次部署目录而报路径错误；本交付不包含这两项本机部署功能，最终明确选取 92 项 hosted 功能测试，原断言未改。
3. **仓库 unit 通过**。提交前 backend 的静态、系统检查、迁移漂移、依赖及版本检查均通过。默认 TimescaleDB template1 初次完整测试仍有此前已诊断的 4 个 time_bucket 垫片冲突；在仓库外配置 TEST.TEMPLATE=template0 后，**完整 `scripts/validate.sh backend` 退出码 0**：571 项，547 通过、24 外部数据库用例跳过。没有修改仓库测试、断言或 settings。
4. **远端只读扫描成功**：Git HTTPS 代理读取 DB-AIOps main，inbox=0、jobs=0，符合当前 DB-001..003 人工承接的排除策略；没有创建真实生产订单或调用模型。
5. YAML 解析、并发组和工作流结构检查、diff 空白检查通过。Xinchuang checkout 无改动。

复现：

```bash
cd /workspace/DB-AIOps
.venv/bin/python -m unittest discover -s tools/o-autorun/tests -v
.venv/bin/python tools/o_db_autorun.py --scan-only --repo . --directory /tmp/o-db-scan
source /workspace/.db-aiops-setup/dev-env.sh
PYTHONPATH=/workspace/.db-aiops-setup/backend-config:/workspace/DB-AIOps bash scripts/validate.sh backend
```

最后一条的仓库外 sitecustomize 只在 integration 测试启动时设置 TEST.TEMPLATE=template0，与已有 onboarding_test_settings 配置一致。该配置不是交付源码依赖，普通 PostgreSQL 的 CI 不需要。

证据：[verification.json](evidence/DB-003-O/verification.json)、[适配测试](evidence/DB-003-O/adapter-tests.txt)、[引擎专项](evidence/DB-003-O/engine-tests.txt)、[后端摘要](evidence/DB-003-O/backend-summary.txt)。

## 未解除条件与精确下一步

- **线上 Actions 未验证**：当前云环境访问 `api.github.com` 返回 Forbidden。Secret 名称查询、原链运行查询均失败，故不能认定 DB-AIOps 的 `O_CODEX_AUTH_JSON` / `O_AUTORUN_STATE_KEY` 已有或缺失，也没有请求重复配置、提取或复制 Xinchuang 秘密。已在环境草稿仅追加 `api.github.com`，保留原网络预设；保存成功但尚未应用或发布。用户在环境设置审阅、保存并发布后需重新测试 API，成功才可查询 Secrets 名称、运行记录并触发实跑。若 Secret 确实缺失，由账号所有者按 README 在自己的电脑安全配置，不在聊天提供值。
- **定时条件未满足**：只读 `git ls-remote --symref origin HEAD` 实测 DB-AIOps 默认分支为 master。GitHub schedule/workflow_dispatch 要求默认分支有工作流；main 推送触发不等同于定时巡检生效。请用户/部门经理决定将默认分支设为 main，或授权独立的默认分支部署方案；本次未擅自修改默认分支或 master。
- **全链路尚未验收**：GorkMan member_poller/分发器、A 链及 O Actions 真实执行就绪后，由 GorkMan 建立不占正式编号的 DB-TEST 单，记录订单提交、分发器可见性、群广播、Actions run ID、O 自动 ETA 回执及报告提交，再归档测试单并回归 Xinchuang。现有合成执行器测试与手动接单不能代替这些证据。
- cloud 原有 CONNECT/环境发布条件没有被此次 runner 适配消除。全链路未通过前，不宣称已实现无人值守闭环。
