# 使用体验改进交付与验证报告

日期：2026-11-18。设计：[EXPERIENCE_DESIGN.md](EXPERIENCE_DESIGN.md)。

## 已交付

1. 重构首页为运维工作台：来源、获取时间、采集时间、今日评分覆盖数、互斥状态、建议入口同屏呈现。失败保留旧值，首次失败使用缺失标识，真实空列表提供下一步；不再把异常吞成零值。
2. 趋势窗口贯通前后端，支持 1/6/24/168 小时，显示 ORM/TimescaleDB 来源和小时样本均值语义。移除没有后端数据的 CPU 选择；旧窗口迟到响应不能覆盖新窗口。
3. 修复统计与 ORM 趋势的数据范围隔离，评分只使用可见启用实例的今日分数，告警也限制同一范围。双存储读取失败明确 503 并留降级记录。
4. 全局功能/实例搜索支持键盘回车和无结果说明，按权限过滤；增加任务指南，移除空的用户菜单入口。小屏默认收起导航，顶部按钮可随时展开/收起。
5. 登录错误就地说明，失效会话登录后回原页面及查询/锚点，阻止外站跳转。旧会话的迟到401不能注销新会话。错误带请求编号；取消请求不重试。
6. 404 明确解释；错误页默认折叠技术细节，更换页面可以恢复。损坏的用户缓存不再直接抛 JSON 解析错误。修正大模型设置页权限映射。
7. 纳管参数修改即撤销旧测试结论，旧参数的晚到成功/失败均不覆盖新参数状态。关闭未保存表单需要确认，保存中不允许关闭。新增配置真正保存周期和模板，前后端统一周期范围，服务端拒绝非法值。
8. 导航树修复不存在的健康 API 调用，移除伪造的默认70分，保留真实零分；系统降级不再显示误导的“0组件失联”。

## 设计评估

方案以状态可见、错误预防、识别优于记忆、最小权限和可恢复性为依据，各项有可证伪的回归条件。未新增模型，无迁移兼容成本。均值不是整体吞吐量，已在界面标注；“获取成功”不等于“采集新鲜”，已分开说明。

未进行真实 DBA 用户研究，不能从自动验收推导满意度、事故处置效率或 MTTR 的提升百分比。UAT 为代理执行的浏览器验收，仍需业务负责人决定生产验收。

## 验证结果

| 层级 | 实际执行 | 结果 |
| --- | --- | --- |
| 编辑循环 | `bash scripts/validate.sh unit --json` | 最终红线扫描与 unit 测试通过 |
| 冒烟 | Django check、HTTP readiness、Vite 3000→Django 8010 API 代理 | 通过；HTTP readiness=200 |
| SIT 后端 | `bash scripts/validate.sh backend`，真实 PostgreSQL 17 | 622 项发现：593 通过、29 跳过、0 失败 |
| 后端检查 | 语法、Django check、迁移漂移、依赖完整性、文档版本 | 全通过；无需新增迁移 |
| 前端 | `bash scripts/validate.sh frontend` | 构建成功；9 个测试文件、85 项通过 |
| 浏览器 UAT | 系统 Chromium + Playwright、真实 Django/PostgreSQL，1440×1000 与390×844 | 11 个流程通过，0 未捕获页面异常；验收创建的实例已清理 |
| 安装可重复性 | `bash scripts/cloud-environment.sh install` 重复安装，database/serve/check 启动与探活 | 通过；已有本地配置未覆盖 |

29 项跳过保持测试运行器的真实结果，未计入通过数。故障注入场景明确标记，未把模拟响应当作真实外部服务成功。

浏览器流程：

- 空用户名校验、错误密码就地提示、正确登录回原地址。
- 输入“事故”按回车进入事故中心。
- 搜索无命中时解释搜索范围。
- 从使用指南进入监控工作台。
- 选择6小时，观察实际 API 参数和响应窗口。
- 注入统计503，保留旧结果并展示请求编号；撤销注入后重试恢复。
- 未知链接显示404并返回。
- 失效会话重新登录保留路径、查询和锚点。
- 纳管取消后继续编辑保留输入；确认放弃后关闭。
- 表单真实连接本地 PostgreSQL；更改参数撤销旧成功；延迟实际连接测试响应并修改参数，迟到结果不能生效；保存后回读120秒周期与模板。
- 小屏导航展开/收起，等待布局稳定，无页面横向溢出，指南可访问。

## 证据和复现

可审阅的结构化结果也保存于 [uat-results.json](uat-results.json)。当前实例截图证据在 `/workspace/dbaiops-evidence/`：`uat-results.json`、`dashboard-desktop.png`、`partial-failure.png`、`dashboard-mobile.png`。日志中可能包含请求令牌，不将未经脱敏的服务访问日志作为交付证据。

复现环境：

```bash
cd /workspace/DB-AIOps
bash scripts/cloud-environment.sh install
# 在单独 shell 保持服务运行
bash scripts/cloud-environment.sh serve
```

在另一 shell 复现测试：

```bash
cd /workspace/DB-AIOps
set -a
source /workspace/dbaiops-runtime/runtime.env
set +a
bash scripts/validate.sh unit --json
bash scripts/validate.sh backend
bash scripts/validate.sh frontend
.venv/bin/python scripts/seed-experience-uat.py
npm install --prefix /workspace/uat-tools --cache /workspace/.cache/npm playwright@1.65.1
node scripts/experience-uat.cjs
```

浏览器使用 `/usr/bin/chromium`，不依赖下载浏览器。账号由本地随机生成并保存在权限0600文件中，不输出密码，不提交；仅用于隔离开发数据库。UAT 中连接信息只从受保护运行配置读入浏览器表单。

## 环境与范围限制

已保存环境草稿的 `install_script`、`start_skill`。安装依赖和当前实例运行已验证；草稿保存不会执行或发布配置。用户仍需在环境设置中检查并保存，再发布环境。没有验证发布后新任务的恢复，不能宣称已完成。

未验证六种真实目标数据库的完整采集矩阵、TimescaleDB/ES实服务、外部LLM、真实通知投递、生产容量和长稳。本环境关闭这些可选集成，验证了 PostgreSQL ORM 回退和真实PG连接。没有执行真实修复或破坏性演练；上述限制不是已经通过的生产验收。

本次重点审阅交互链路及相关接口，不宣称逐行审计数百个源码文件。本次实现覆盖设计表格所列的体验改进，不宣称全部历史功能、所有数据库方言或每个页面的每条分支都做过人工验收。

当前 checkout 基于用户选择的 main，而 AGENTS.md 的旧工作流指定推 master；不把本次 main 基线和改动推向另一条已有分支。代码与报告保留在当前 checkout 供审阅。
