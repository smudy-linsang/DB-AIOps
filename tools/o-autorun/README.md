# DB-AIOps O 自动领单

本目录固定复用 Xinchuang `84a40d93e663a1844fb3500eaf921efc41b54a88` 的 runner/协调器实现；不读取或写入 Xinchuang 的队列。原仓库和原工作流均未修改。来源及校验值见 `provenance.json`。

入口为 `tools/o_db_autorun.py`，工作流为 `.github/workflows/o-autorun.yml`。队列加密状态写在 **DB-AIOps** 的 `o-autorun-state` 分支，concurrency group 独立。GitHub token 只使用本仓库的短期 `github.token`，无需跨仓库 PAT。

- main 的 orders/reports/dispatch 推送触发；计划每 5 分钟兜底，可手动触发。
- **schedule/workflow_dispatch 要求该工作流存在于默认分支**。本仓默认分支若仍是 master，不能宣称定时兜底已运行；请由用户/部门经理把默认分支切到 main，或批准专门的默认分支部署方案。不要擅自把黑板改读 master。
- `O_CODEX_AUTH_JSON`、`O_AUTORUN_STATE_KEY` 是本仓 Actions Secret 名称，不是环境草稿 Secret。必须先查已配置的名称，缺少才补，不从 Xinchuang 导出或复制值。官方账号所有者设置脚本：`python tools/o-autorun/configure-github.py --repo smudy-linsang/DB-AIOps --executor runner --no-canary`；仅在账号所有者自己的电脑执行，不在注入平台身份的云容器运行。
- 默认 runner；cloud 仍受既有平台环境/网络条件限制。没有自动调用模型的本地验证：`python tools/o_db_autorun.py --scan-only --repo . --directory /tmp/o-db-scan`。

订单只处理直接位于 `docs/pm/orders/` 的 `DB-数字-*.md` 与 `DB-TEST-标识-*.md`，精确识别独立 `执行：` 行中的 O，支持多人分工。DB-001..003 已人工承接，自动扫描排除；**显式 dispatch 可以覆盖历史排除，并保留原有 source/round/requires 契约**。自动模式把该订单最后修改提交固定为 source_sha，报告更新不会重复提交。需要换轮次或指定被审 SHA、等待 GG/A 凭证时，应由任务发布人添加显式 dispatch，不能猜测前置已满足。不要仅编辑旧单就期待覆盖原报告：原引擎保留禁止覆盖报告的规则，换轮次需显式 dispatch。

正式执行前先独立推送 `DB-xxx-Rn-ack-O.md`，含身份键和 ETA；写回失败不会继续启动模型。回执不能与最终报告等同，3 小时 ETA 到期后若执行失败，应检查 Actions 及 state 分支公开状态，不把超时解释为通过。报告仍经引擎白名单、哈希和三档结论验证后推送 main。读取错误会令工作流报错，不当空目录处理；队列不会因失败扫描消失。

适配测试：`python -m unittest discover -s tools/o-autorun/tests -v`。9 项测试包含本地真实 Git 的发单、ETA 回执、**合成执行器**、验证发布及重扫去重；不是模型实跑、飞书广播或线上全链路验收。原引擎只做两处 Linux/仓库红线兼容替换（异常分支 `pass` 改为等价的循环继续/进程状态读取），保留上游协议与校验。

全链路由 GorkMan 使用临时 DB-TEST 单验收，记录订单提交、分发器可见性和群广播、Actions run ID、自动回执与报告提交，然后归档测试单。当前不会以手动扫描或合成执行器代替该验收。

## 2026-11-05 自动链修复

负责人行兼容独立 `执行：O`、Markdown 列表 `- 执行：O`（也接受 `*`/`+` 列表及半角冒号）；仍精确匹配 O，出现多个相互冲突的负责人行时拒绝收单。当前默认分支已由用户改为 main，旧文档的 master 默认分支限制属于历史情况。

每次 Actions 在授权检查后与结束时，将白名单状态写入本仓 `o-autorun-health` 分支的 `health.json`：run URL、源码 SHA、测试/扫描/授权/安装/沙箱/执行结果及缺失的 Secret **名称**。不记录 Secret 值、模型提示词或原始日志。状态分支既不触发 main 工作流，也不替代真实任务报告。

只读查询（无需 Actions API）：

```bash
git fetch origin refs/heads/o-autorun-health
git show FETCH_HEAD:health.json
```

若授权阶段失败，先依据健康记录的 `missing_owner_secret_names` 核实缺少的本仓 Actions Secrets，再由账号所有者执行安全配置；不得从 Xinchuang 的加密状态提取或复制凭据。不能把本地环境里没有这些变量当作 Actions 未配置的证据。

自动执行结果先保存在不可覆盖的轮次报告和机器 receipt 中。如果订单明确写了本任务 `docs/pm/reports/DB-xxx-report-O.md` 固定交付路径，协调器在该任务 done 后，从已验证的 published_sha 读取原始报告并逐字节交付到该路径。校验来源身份/哈希、拒绝符号链接和同名不同内容；同内容重复运行不会再提交，也不会调用模型。原轮次报告和 receipt 保留，固定入口不是新一轮评审。
