# WAKE-SETUP 报告 v2.0（HYMan / WorkBuddy）

- 任务单：`docs/pm/orders/DB-HY-001-HYMan入队.md`
- 执行体：WorkBuddy（腾讯混元 Hy）；"HYMan"作为本协作体系内代号
- 报告时间：2026-11-17
- 结论：**有条件通过**
- 授权依据：需求方 2026-11-17 明示"自有可信环境、接受上述风险，实现无人值守全自动"，据此接通自动链；v1.0 所列风险由需求方知情接受。

> 只记录已实测事实。未执行、无法执行或环境不具备的项据实标注，不编造结果。本报告不含任何密钥、凭据、令牌（仅列 open_id 非敏感标识与 token 状态）。

## 一、环境实测

| 项 | 实测值 |
|---|---|
| 宿主机系统 | Windows（win32） |
| Python 版本 | 3.13.14（`python --version` 实测） |
| 仓库工作区路径 | `C:\workbuddy\DB-AIOps`（按需求方指定，非任务单里的 `C:\DB-AIOps`） |
| 仓库 HEAD（本报告基线） | `eac73e4`（v1.0 报告已 push，main 远端已确认收到） |
| clone / fetch | 完成，`git fetch origin main` 正常 |
| Git 提交身份 | 沿用仓库历史作者 `lqlinsang <linsang@gmail.com>`（仅设本仓库 local config，未动全局） |

## 二、任务单各步骤状态（v2.0）

| # | 任务单要求 | 状态 | 依据 |
|---|---|---|---|
| 1 | clone 黑板仓库到本机、fetch 能通 | **完成** | 见 §一 |
| 2 | 装轮询守护：`member_poller.py` 拷到固定目录、设 `MEMBER=HYMan` | **完成** | 守护脚本原本就在本机 `C:\dispatcher\member_poller.py`（不在仓库内，这是 v1.0 说"仓库无此文件"的原因）；已在其 `ALIASES` 增加 `"HYMan": "HYMan"`，并加入一条 HYMan 自测断言。脚本按任务单要求只驻留本机固定目录，**未纳入仓库**（仓库 `scripts/lint_redlines.py` 禁止 `except: pass` 模式，而该调度脚本含两处；且任务单只要求本机目录）。原文件已备份为 `member_poller.py.bak-preHYMan-20261117` |
| 3 | `python member_poller.py --self-test` | **完成，16/16 通过** | 实测输出见 §三。任务单写的"9 项"是旧口径，当前脚本实际 16 项，按实测报告 |
| 4 | `python member_poller.py --member HYMan --workdir ... --once` 试跑 | **完成** | 首次运行记基线 40 个任务单、不叫醒，符合脚本设计；实测输出见 §三 |
| 5 | Windows 任务计划程序建"登录时启动"常驻任务 | **未采用该方式**（改用等效机制） | `schtasks.exe` 被本机安全策略列入程序黑名单，无法也不应绕过；改用本环境原生 automation 定时驱动（见 #8），功能等价且可审计 |
| 6 | 把 WorkBuddy 任务工作空间指向 `C:\DB-AIOps` | 部分满足 | 工作区为会话/工作台管理项；clone 在 `C:\workbuddy\DB-AIOps`，automation 的 cwds 已指向该路径，本次及定时任务均可直接读写 |
| 7 | 绑定飞书助理（Claw 远程遥控） | **通道已连通；"Claw 助理绑定"未执行** | 见 §四：飞书 connector connected、lark-cli 凭证有效（bot/user 均 ready）；但无 OpenClaw/Hermes agent workspace，无宿主预置 app，无法完成"飞书消息→叫醒本 WorkBuddy"的助理绑定。无人值守领单改由本地 automation 驱动，不依赖飞书叫醒 |
| 8（可选） | 每 10 分钟定时任务：读 trigger 与 orders，有新单就执行 | **已建立（每小时）** | 见 §五：automation 已创建并 ACTIVE；调度最小粒度为每小时，非 10 分钟，如实标注 |

## 三、实测输出原文

### 3.1 `member_poller.py --self-test`（Python 3.13.14）

```
[member_poller] WAKE-UP: 新任务单 docs/pm/orders/T-003-mine.md 路由到你 (GG)
trigger 文件已写入: C:\Users\linsa\AppData\Local\Temp\tmpfi71nuf9\wake_trigger.json
[PASS] parse 执行：GG
[PASS] parse gork-team
[PASS] parse D-team lead 行
[PASS] parse M-team
[PASS] parse HYMan
[PASS] 多方执行全识别
[PASS] 八进制转义解码
[PASS] 普通文件名不受影响
[PASS] C 单字不误命中
[PASS] 首次运行只记基线不叫醒
[PASS] 新任务单叫醒一次
[PASS] trigger 文件已写
[PASS] trigger 内容对版
[PASS] 幂等: 第二轮不再叫醒
[PASS] Windows 无窗口标志存在
[PASS] Windows 隐藏窗口 STARTUPINFO 存在
self-test: 16/16 passed
```
退出码 0。（首行 WAKE-UP 横幅来自自测中 `process_board` 的 trigger 分支打印，属预期。）

### 3.2 `--once` 首次试跑（DB-AIOps）

```
[member_poller] member=HYMan repo=smudy-linsang/DB-AIOps workdir=C:/workbuddy/DB-AIOps interval=60s state=C:\dispatcher\member_poller_state_HYMan.json
[member_poller] 首次运行已记基线: 40 个已有任务单不叫醒
```
退出码 0。状态文件 `C:\dispatcher\member_poller_state_HYMan.json` 已生成，`seeded=true`，无叫醒。

## 四、飞书通道核实（不谎报）

| 检查 | 结果 |
|---|---|
| WorkBuddy 飞书 connector | connected / bound / enabled（connector 状态） |
| lark-cli 凭证 | 已配置，appId `cli_aa739ba187f89bf7`，bot 与 user 身份均 `ready`，token `valid`（`lark-cli auth status --json`、`whoami --as bot` 实测） |
| 环境是否为 agent workspace | 否：`OPENCLAW_HOME` / `HERMES_HOME` / `LARK_CHANNEL` 均未设置，workspace=local |
| "绑定飞书助理（Claw 远程遥控）" | **未完成**：该绑定要求 agent workspace + 宿主预置 app + 先与用户确认身份预设（bot-only / user-default）。当前无 agent workspace、无宿主预置 app，不能也不应自行完成一次代理级绑定 |
| 对无人值守领单的影响 | 无阻塞：领单由本地 automation 轮询驱动，不依赖飞书叫醒。飞书通道当前可用于广播/通知，但"飞书消息直接叫醒本 WorkBuddy 执行"这一具体能力未验证、未声称可用 |

## 五、无人值守机制（替代被禁的 schtasks）

- **机制**：本环境 automation（`FREQ=HOURLY;INTERVAL=1`，ACTIVE），ID `b3ec90a8-38f5-4664-9023-5d0ac46eaa11`，工作区 `C:\workbuddy\DB-AIOps`。
- **动作**：每小时 `git fetch` → 运行 `member_poller.py --once` → 若 `wake_trigger.json` 本轮更新（有新单派给 HYMan）则按黑板规矩领单：回执+ETA → 读 STATUS/DECISIONS → 执行 → 写报告（三档结论）→ `validate.sh unit` → commit & push main → 看 CI；无新单则不动任何文件。
- **偏差（如实）**：任务单要求"每 10 分钟"，本平台调度最小粒度为每小时，故降为每小时；`schtasks.exe` 登录自启被安全策略禁止，未绕过。
- **安全约束写入自动化提示词**：不回显任何凭据/token/密钥；缺环境如实记录、不编数；只处理「执行：HYMan」的单。

## 六、变更清单

- `docs/pm/reports/WAKE-SETUP-report-HYMan.md`（本报告，v1.0→v2.0）
- 本机 `C:\dispatcher\member_poller.py`（原脚本加入 HYMan 别名与自测断言；已备份 `.bak-preHYMan-20261117`；未纳入仓库，见 §二#2）
- 本机 `C:\dispatcher\member_poller_state_HYMan.json`（首次基线状态）
- automation `HYMan 黑板无人值守轮询与领单`（新建，ACTIVE，每小时）

## 七、未尽事项（非否决）

1. 飞书"Claw 助理绑定"待宿主提供 agent workspace / 预置 app，并与用户确认身份预设后再做；当前未做、不谎报。
2. 轮询间隔为每小时（平台下限），非任务单所述 10 分钟；如需求方要求更密，需平台支持分钟级调度或宿主放行 schtasks。
3. 定时任务尚未经过一次"真实新单"触发的完整端到端跑通（当前无派给 HYMan 的新单）；机制与自测已验证，端到端待首单实测。
4. 本仓库本次仅有 Markdown 文档变更，无代码改动；`scripts/validate.sh unit` 的**红线扫描通过**，但 unit 测试阶段因本机未安装 Django（无 venv、`import django` 失败）无法跑通，属本机环境缺失、非文档改动引起；未强行安装依赖。`scripts/scan_secrets.py` 对报告扫描通过。

## 八、结论三档判定

**有条件通过**：clone、守护安装、self-test 16/16、once 试跑、无人值守 automation 均已实测完成；条件为 §七 三项（飞书助理绑定待宿主环境、调度间隔为每小时、端到端待首单实测），条件补齐后可转通过，不必重走整套验收。
