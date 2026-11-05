通过

# DB-012 报告：ci.yml 监听 main（A，执行 DEC-006）

- 成员：A（Claude Code）｜日期：2026-11-05｜人工会话｜回执：`DB-012-回执-A.md`
- 改动提交：`0150511`（main）

## 结论：通过

`ci.yml` 的触发分支已改为 `main`。直推 main 的提交已经实际触发 CI，7 个 job 全部真跑，DB-012 的验收目标达成。

当前 main 的 CI 是红的，但原因与本单无关：前端依赖门禁命中一条新公布的漏洞通告，详见下方「当前 main 红灯（非本单引入）」。

## 改动（只改触发分支）

```diff
   pull_request:
-    branches: [master]
+    branches: [main]
   ...
   push:
-    branches: [master]
+    branches: [main]
```

- job 内容未改：方言、Oracle 两个 job 的运行条件是 `github.event_name == 'push' || 带 test-dialect 标签`，不含分支名，所以在 main 推送上照常运行。
- 两处注释里的 master 字样按"只改触发分支"未动。
- master 分支未动，默认分支未操作（用户已切为 main）。

## 本地证据

| 命令 | 结果 |
|---|---|
| `scripts/validate.sh unit --json` | exit_code 0（红线扫描、unit 测试） |
| `scripts/validate.sh all --json`（本地 PostgreSQL 16） | exit_code 0：语法编译、Django 系统检查、迁移漂移、依赖完整性、文档版本、全量测试、前端构建、前端测试全部 pass |
| YAML 解析 | `push.branches == ['main']`，`pull_request.branches == ['main']` |

## CI 实测

| run | 提交 | 结果 |
|---|---|---|
| [#99 41948091674](https://github.com/smudy-linsang/DB-AIOps/actions/runs/41948091674) | `0150511`（本单） | **由本单推送触发**，证明 main 推送已能触发 CI。<br>静态检查、安全扫描、单元测试为 success；前端构建与测试为 failure（见下）。<br>集成、方言 MySQL/PG、方言 Oracle 三个 job 在运行中被 #100 按 `cancel-in-progress` 取消：其他成员 01:24 推了 DB-013 的提交。 |
| [#100 41948261128](https://github.com/smudy-linsang/DB-AIOps/actions/runs/41948261128) | `71db4fa`（含本单改动） | 7 个 job 全部真跑。<br>success：静态检查、安全扫描、单元测试、集成测试（含随机顺序复跑）、方言 MySQL/PG、方言 Oracle（"确认用例真的跑了"通过）。<br>failure：前端构建与测试。 |

## 当前 main 红灯（非本单引入）

- **失败点**：「前端构建与测试 → 前端依赖漏洞门禁」，即 `npm audit --audit-level=high` 报出 `follow-redirects 1.16.0 - 1.16.1`（high，[GHSA-mrwp-f3q8-3g9p](https://github.com/advisories/GHSA-mrwp-f3q8-3g9p)）。另有 vitest 的 2 条 moderate，不触发门禁。
- **为什么判定非本单引入**：本单 diff 只改 ci.yml 两行，不涉及依赖；锁文件里的 `follow-redirects@1.16.1` 是 10 月门禁修复时的安全版本。现在红，是因为通告库新增了这条 high 级通告。
- **修复已备好、未推送**：
  - 改动：`frontend/package-lock.json` 把 follow-redirects 升到 1.16.2，3 行（version / resolved / integrity）。
  - 本地验证：`npm ci` 后 `npm audit --audit-level=high` exit 0，`validate.sh frontend` exit 0。
- **待确认**：这个依赖修复超出 DB-012 "只改触发分支"的范围，是否由 A 直推，待用户 / 部门经理确认。AGENTS.md 要求"看到红立刻修或回滚"，回滚本单并不能消除红灯。
