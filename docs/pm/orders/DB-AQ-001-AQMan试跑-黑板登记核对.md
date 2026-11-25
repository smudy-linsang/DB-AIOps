# DB-AQ-001 AQMan试跑：黑板登记核对（只读）

执行：AQMan

## 背景
AQMan 2026-11-25 入队（AWS Quick 智能体，用户转达入队）。本单为首单试跑，只读核对黑板登记，验证“读单—核对—写报告—回黑板”全链路是否跑通。接单链路为人工转达版：用户先在本机 pull 最新，再把单号告诉 AQMan；AQMan 把报告写到本地 reports/，由用户 commit/push 回黑板。

## 前置条件
开始前，用户先在 Smudy 本机执行 git pull，把 C:\AWSQuick\DB-AIOps 更新到最新，再告单号。本地库过期会导致数字对不上。

## 任务（只读）
1. 读本地 docs/pm/STATUS.md，记下版本号（文件末尾版本记录里的最新版本），逐条列出“进行中”一节的全部单号。
2. 数 docs/pm/orders/ 下的任务单文件数与 docs/pm/reports/ 下的报告文件数；子目录（evidence 等）里的文件单算并注明。
3. 读 docs/pm/reports/DB-030-report-M-team.md，抄录首行结论（通过/有条件通过/不通过）与正文 D-01、D-02 两项复验结论。
4. 用 GitHub 网页逐个核对以下 5 个提交号是否在 main 分支上，并抄录每个提交的标题第一行：62c3a49、1db58ad0、4e8dd92、28e82325、888bb70。
5. 确认 docs/pm/reports/ 下无残留测试文件（_AQMan-write-test.tmp 应已删除，若还在，写明）。

## 交付
报告写到本地 docs/pm/reports/DB-AQ-001-report-AQMan.md。首行写结论（三档之一：通过/有条件通过/不通过）。正文逐项贴证据：数字写清来源（本地文件路径或 GitHub 页面链接），原文引用照实抄。末尾单列“未测/未核实”清单。写好后告用户，由用户 commit/push 回黑板。ETA：2026-11-25 当天。

## 约束
- 只读：不改 STATUS.md、DECISIONS.md、ROSTER.md 与任何任务单；不删、不建黑板文件（本单的报告文件本身除外）。
- 只认实测：每个数字写清来源；推断标“推断”，未核实标“未核实”。
- 报告里不要写密码、token、密钥等敏感值。
- 有问题先报 PM（由用户转达），不要直接改板。
