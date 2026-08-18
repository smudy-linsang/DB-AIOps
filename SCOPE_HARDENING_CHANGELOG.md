# 数据范围加固与门禁补齐（复测建议落地）

**实施日期**：2026-08-18
**对应来源**：`V2.5_REMEDIATION_RETEST.md` §3 RT-01 与 §4 的三条残留建议
**实施者**：独立复审方（即提出这些建议的人）

---

## 背景：同一类越权复发了四次

| 轮次 | 编号 | 表现 |
|------|------|------|
| 初次全量审计 | BUG-103 | 性能中心接口未校验数据范围 |
| v2.0 复审 | REV-01 | 新写的 v2 接口族又忘了 |
| v2.5 复审 | R25-01 | 新写的 Copilot 工具层又忘了 |
| v2.5 整改复测 | RT-01 | 既有的容量/拓扑总览一直没有 |

四次的成因完全相同：**数据范围靠"每个视图记得调 helper"，换个楼层就忘。**
v2.5 已经引入 `visible_to()` 原语，但只在一处使用 —— 原语建好了没推广，
等于把同一个坑留在了原地。

本次做三件事：**堵住现存的洞 → 把原语推广成唯一写法 → 让"忘记"在提交时就被拦下。**

---

## 一、修复 RT-01 并统一到 `visible_to()`

### 1.1 补权限校验

以下五个视图此前**只有 `require_auth`** —— 任何登录账号（含零权限只读角色）
都能调用：

| 视图 | 补充的权限 |
|------|-----------|
| `CapacityOverviewView` | `metrics.view` |
| `TopologyOverviewView` | `databases.view` |
| `DashboardStatsView` | `metrics.view` |
| `DashboardChartsView` | `metrics.view` |
| `AlertAvailableMetricsView` | `metrics.view` |

已核对 5 个内置角色（super_admin / dba / auditor / config_operator / readonly）
均持有这两项权限，**不会锁死任何既有角色**；只有自定义角色缺权限时才会被挡。

### 1.2 补数据范围

五处查询统一改为 `DatabaseConfig.objects.visible_to(request.user)`。

拓扑视图额外处理了两条隐蔽路径 —— 它的节点不是从 `DatabaseConfig` 直接取的，
而是经 `DatabaseTopology` 与 `peer_databases` 间接取：

```python
visible_ids = set(DatabaseConfig.objects.visible_to(request.user)
                  .values_list('id', flat=True))
topologies = DatabaseTopology.objects.filter(db_config_id__in=visible_ids)...
...
for peer in topo.peer_databases.all():
    if peer.id not in visible_ids:
        continue          # 邻接节点不得经由拓扑外泄（连边也一并跳过）
```

**只挡住主查询是不够的** —— 通过邻接关系照样能读到范围外实例的名称与主机。

### 1.3 让原语真正唯一

把各自拼装数据范围的地方也收敛过来，避免"两套写法并存"：

| 位置 | 改动 |
|------|------|
| `api_views_phase8._get_config` | 手拼 `allowed` 判断 → `visible_to()` |
| `api_views_v2._scoped_database` | 同上 |
| `api_views_v2._scoped_incident` | 改为 `config__in=visible_to(...)` |
| `api_views.DatabaseListView` | 去掉重复的 `allowed_db_ids` 过滤 |

### 1.4 实测

```
有权限但范围受限的账号（只授权 probe-mine）：
  /api/v1/capacity/overview/                200  泄露=否
  /api/v1/topology/overview/                200  泄露=否
  /api/v1/dashboard/stats/                  200  泄露=否
  /api/v1/dashboard/charts/                 200  泄露=否
  /api/v1/alert-rules/available-metrics/    200  泄露=否

零权限账号：
  /api/v1/capacity/overview/  → 403
  /api/v1/topology/overview/  → 403
```

---

## 二、新增静态规则：`[未套数据范围]`

`scripts/lint_redlines.py` 增加第五条规则 `check_unscoped_config_queries`：

- **范围**：`api_views*.py` / `views_enhanced.py` 中**形参含 `request`** 的函数；
- **判定**：出现 `DatabaseConfig.objects.{all,filter,exclude}` 且表达式链里没有 `visible_to`；
- **只看新增行**（与其余规则一致，不对存量代码刷噪音）；
- **豁免**：确属全局查询的加 `# scope-check: allow <理由>`。
  目前仅两处名称唯一性校验用了它。

**验证方式是注入再回滚**：临时加一个"忘记套范围"的视图，扫描如实报出

```
[未套数据范围] monitor/api_views.py:4457  请求路径查询 DatabaseConfig 未经 visible_to()
退出码=1
```

移除后恢复通过。

---

## 三、红线门禁覆盖整段推送

原实现在 CI 下扫 `HEAD^..HEAD`，**一次 push 含多个提交时只覆盖最后一个**，
前面提交里的违规会直接漏过。

改动：
- `_diff_args()` 支持 `REDLINE_DIFF_BASE` 环境变量作为基线；
- CI 传入 `${{ github.event.before }}`，并把 `fetch-depth` 从 2 改为 0；
- 保留 `HEAD^` 兜底 —— 新分支首推时 `before` 是全零、force-push 后旧基线可能不可达。

**实测**（克隆仓库，构造"3 个提交、违规在第 1 个"的推送）：

```
旧行为（仅 HEAD^..HEAD）      : 通过 ← 漏掉
新行为（REDLINE_DIFF_BASE）   : 拦截 ✔  [except-pass] monitor/degrade.py:45
基线全零（新分支首推）        : 回退 HEAD^ 后通过（预期）
```

> 实现过程中一处自我修正：最初 `_has_commit()` 用 `rev-parse --verify <rev>^{commit}`，
> 与 v2.5 既有用例 `test_ci_clean_checkout_scans_parent_commit` 的 `_git` 桩语义不符，
> 导致该用例失败。**既有用例是有效的回归防线，我改自己的实现去适配它，
> 而不是改测试来迁就我的写法。**

---

## 四、删除死代码

`playbook_engine_v2.DEFAULT_PLAYBOOKS` 与 `init_default_playbooks()`（53 行）已删除。

种子数据在 v2.5 整改时已移入 migration `0030`（`get_or_create`，保留 DBA 自定义），
该函数自此无任何调用点。留着会让人误以为运行期还有 bootstrap，故清除并在原处
留注释说明去向。

---

## 五、回归防线

新增 `monitor/tests_scope_hardening.py`（7 条）：

| 用例 | 守住什么 |
|------|---------|
| `GlobalOverviewScopeTests`（3） | 五个全局视图不向范围外泄露实例名称/主机 |
| `GlobalViewPermissionTests`（2） | 零权限被 403；有权限不被误伤（防过度收紧） |
| `RedlineScopeRuleTests`（2） | 静态规则对现网代码零告警；`REDLINE_DIFF_BASE` 生效且全零回退 |

**关键设计**：范围用例必须用**"有权限、但数据范围受限"**的账号。
若用零权限账号，会先被 403 短路，"没泄露"是权限拦下的结果，
**数据范围这一维根本没被测到** —— 权限与范围是两道独立的闸，必须各测各的。

**做过变异验证**：临时回退 `CapacityOverviewView` 的 `visible_to`，
用例立即失败（2 条：范围用例 + 静态规则用例），确认不是空转的绿灯。

---

## 六、验证

| 项 | 结果 |
|---|---|
| 后端全量 | **263 passed** / 21 skipped（较整改前 +7） |
| 随机顺序复跑 | OK |
| `manage.py check` / 迁移漂移 | 通过 / 无漂移 |
| 红线扫描 / 密钥扫描 / 依赖完整性 | 全部通过 |
| `ruff --select F821,F601,F811` | 通过（F841 数量与改动前一致，未新增） |
| 前端 build / vitest | 通过 / 12 passed |
| 越权功能探针（5 个端点 × 两类账号） | 见 §1.4 |
| 多提交推送门禁实验 | 见 §3 |
| 变异验证（回退 visible_to） | 用例如期转红 |

---

## 七、遗留

`AGENTS.md` 已补入两条写法约定（`visible_to` 是唯一原语；权限与范围要分开测）。

静态规则目前只覆盖 `api_views*.py` / `views_enhanced.py`。若将来新增其他
请求处理模块（如新的 `api_views_v3.py` 会自动覆盖，但换个命名就不会），
需要同步扩展 `SCOPED_QUERY_FILES`。这是已知边界，不是疏漏。
