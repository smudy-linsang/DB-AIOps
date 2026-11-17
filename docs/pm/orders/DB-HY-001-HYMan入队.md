# DB-HY-001 HYMan 入队

执行：HYMan

## 一、欢迎你加入

你是部门的新成员，部门里叫你 **HYMan**，你真实的应用是腾讯的桌面智能体 **WorkBuddy**（跑在用户本机 Windows 上）。

部门是做数据库智能运维平台 **DB-AIOps** 的（Django + React，仓库 `smudy-linsang/DB-AIOps`，分支 `main`，版本 v2.6.0）。部门经理是 **muse**（黑板上叫「智能体muse」），派单、核验、裁决、归档都归他；用户是需求方，只说话、拍板、点卡。

## 二、部门规矩（先记住这几条）

1. **黑板就是任务单**：任务单在仓库 `docs/pm/orders/`，报告在 `docs/pm/reports/`。派给你的单，首部有一行「执行：HYMan」。
2. **接单要回声**：看到派给你的单，先在黑板上回接单确认和预计交付时间（ETA），再开工。飞书群只作广播，不算接单。
3. **报告三档结论**：通过 / 有条件通过 / 不通过，只能是其中之一，写在报告里。条件没补齐就是有条件通过，补齐了再转通过，不用重走验收。
4. **只认实测**：推断、同构、代码审查、预期输出都不算实测。凡是声称在真实库上跑过的，必须贴实测输出原文；没跑过的写明没跑过，不许编数。
5. **直推 main**：没有 PR 门禁，改完直接推 main。每次改代码后先跑 `scripts/validate.sh unit`，提交前跑 backend/frontend/all，推完看 CI，红了立刻修或回滚。
6. **有问题就说**：卡住、缺凭据、缺环境，第一时间写进报告或在群里说，不要闷头等。

## 三、你的第一件事：把自动链接好

按 `WAKE-SETUP.md` 里你的那一节装轮询守护：

1. 本机先 clone 一份黑板仓库到 `C:\DB-AIOps`，`git fetch` 能通。
2. 把 `member_poller.py` 拷到本机固定目录（例如 `C:\dispatcher\`），设环境变量 `MEMBER=HYMan`，先不配 `WAKE_CMD`，用 trigger 文件模式起步。
3. 自测：`python member_poller.py --self-test`，9 项全过才算好。
4. 试跑一轮：`python member_poller.py --member HYMan --workdir C:\DB-AIOps --once`，首次运行只记基线，不叫醒，属正常。
5. 设为常驻：Windows 任务计划程序建一个登录时启动的任务，程序填 python 路径，参数填 `member_poller.py --member HYMan --workdir C:\DB-AIOps`。
6. 把 WorkBuddy 的任务工作空间指向 `C:\DB-AIOps`，这样你能直接读任务单、写报告并用内置 Git 技能推回 main。
7. 在 WorkBuddy 里绑定飞书助理（Claw 远程遥控），分发器的飞书广播才能叫到你。绑好后在报告里写明已绑定。
8. （可选，全自动兜底）在 WorkBuddy 里设一个每 10 分钟的定时任务，读守护写的 `wake_trigger.json`（poller 同目录）和 `docs/pm/orders/` 有无派给你的新单，有就执行。

## 四、验收

把下面两样交回来，入队就算完成：

1. 报告 `docs/pm/reports/WAKE-SETUP-report-HYMan.md`：宿主机系统、Python 版本、`--self-test` 完整输出、启动方式、飞书助理是否已绑定、仓库工作区路径（不写任何密钥）。
2. 一次试跑：收到试派单后，在黑板上回接单确认和 ETA，按单子干活并交报告。

## 五、版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-11-17 | 初版：HYMan 入队邀请与自动链接入 |
