# DB-009 证据清单（DM8 方言对齐 + GBase 矩阵）

本目录是 DB-009 的全部实测证据。**所有结论只以本目录内的实测产物为准**；
报告里出现的每个数字都能在这里找到对应文件。

## 脱敏口径

与 DB-005R 同一口径：本机地址、端口、账号、主机名、实例名、用户目录统一掩码为
`<local-host>` / `<local-host>:<ephemeral-port>` / `<dm-port>` / `<gbase-port>` /
`<account>` / `<dm-admin>` / `<client-host>` / `<gbase-instance>` / `<user-home>`。

- **口令全程不在证据里**：只经 `DatabaseConfig.get_password()` 在内存解密，
  或经环境变量传给子进程；产物里只有 `len=N,head=***`。
- **DM8 系统 schema 名 `SYSDBA` 作为 SQL 字面量保留**：它出现在
  `OWNER NOT IN ('SYS','SYSTEM','SYSDBA','SYSAUDITOR','SYSSSO')` 这样的 SQL 里，
  是查询固定字面量而非账号凭据，掩码会破坏 SQL 证据的可复现性。
- 敏感扫描：本机地址 0 命中、端口 0 命中、账号 0 命中、口令类关键字 0 命中。

## 三类文件

| 类别 | 判定 | 文件 |
|---|---|---|
| **实测产物** | `.json` / `.txt`，是某次实跑的输出 | `db009_*.json` / `db009_*.txt` |
| **可复现脚本** | `.py`，可在同环境重跑并重新生成产物 | `db009_*.py` |
| **过程脚本** | `.py`，是 CLI 会话的一次性改写/修复工具，**不是验收脚本，结论不依赖它** | `db009_apply_dialect_fixes.py`、`db009_repair_newlines.py` |

`manifest.json` 是逐文件的说明 + 字节数 + 脱敏标记。

## 复现前提

1. 仓库根可用（默认 `C:\DB-AIOps`，可用环境变量 `DB_AIOPS_ROOT` 覆盖）。
2. DM8 实例在 `<local-host>:<ephemeral-port>`（平台库存密里的 `db_type='dm'` 实例，id=9）；
   GBase 8a 在 `<local-host>:<ephemeral-port>`（`db_type='gbase'`，id=11）。
3. 平台侧的两个库凭据必须已入库（脚本自己解密，不接受明文口令参数）。
4. `db009_dm_sql_audit_dmpython.py` 额外需要一个**能 import dmPython** 的解释器
   （本机：`<user-home>\AppData\Local\Python\pythoncore-3.14-64\python.exe`）。

## 复现命令

```bash
# ① DM8 SQL 全量审计（pyodbc 通道）
python db009_dm_sql_audit.py

# ② DM8 SQL 全量审计（达梦官方 dmPython 驱动通道）
python db009_dm_sql_audit_dmpython.py

# ③ 订正前 / 订正后 对照
python db009_dm_before_after.py

# ④ 检测器级指标快照（DM + GBase，订正后）
python db009_checker_run_final.py

# ⑤ 端到端（sentinel / start_monitor 的 DM 与 GBase 分支）
python db009_e2e_verify.py

# ⑥ GBase 真实会话并发采样
python db009_gbase_ash_live.py

# ⑦ GBase 游标状态对照试验
python db009_gbase_cursor_state.py

# ⑧ DM8 动态视图列名 / 缓存命中率口径核对
python db009_dm_viewcols.py
python db009_dm_cachehit_ratio_check.py
```

## 关键实测结论索引

| 结论 | 证据文件 |
|---|---|
| 订正前 dm.py 52 条 SQL 中 **29 条**在真 DM8 上跑不通（-2106 × 15 / -2111 × 13 / -2007 × 1） | `db009_dm_before_after.json` |
| 订正后 **47/47 全通过**（pyodbc 通道） | `db009_dm_sql_audit.json` |
| 订正后 **47/47 全通过**（达梦官方 dmPython 2.5.32 驱动通道） | `db009_dm_sql_audit_dmpython.json` |
| DM8 里 **22 个 V$ 视图不存在**（-2106） | `db009_dm_inventory.json` |
| DM8 数据字典**不列 V$ 动态视图**，列名只能靠 `cursor.description` 取 | `db009_dm_dict_no_dynamic_views.json` / `db009_dm_viewcols.json` |
| 缓存命中率：本方公式 **98.29%** ≡ DM8 自带 `RAT_HIT` 加权 **0.9829**（逐池一致） | `db009_dm_cachehit_ratio_check.json` |
| 检测器级：DM **67 keys**（none_scalar=1 = `login_count` 不可得，empty_list=12） | `db009_checker_run_final.json` |
| 检测器级：GBase **51 keys**，`active_connections=1` 已补齐，none_scalar=0 | `db009_checker_run_final.json` |
| GBase ASH **采到 1 行真实会话**（wait_class=on_cpu），不抛错 | `db009_gbase_ash_live.json` |
| GBase 阻塞检测在 GBase 上**不可用**（performance_schema 与 information_schema 两条路径均缺表，已 degrade 留痕） | `db009_gbase_ash_live.json` |
| GBase 游标状态污染（v1 曾出现 `golden_metrics` 返回 `{}`）**三组对照均未复现** | `db009_gbase_cursor_state.json` |

## 两条必须说清的边界

1. **dmPython 缺失的根因不是"拿不到官方授权包"** —— 达梦官方 wheel **可得**，本机已装
   dmPython 2.5.32，但它装在**用户级 Python 3.14**（cp314 ABI）；平台运行时
   （`runserver` / `start_monitor`）用的是 `.venv`（**Python 3.11**，cp311 ABI），
   cp314 的 `.pyd` 在 cp311 里加载不了 ⇒ `import dmPython` 失败 ⇒ `DamengChecker`
   不注册。复通条件见报告第六章。
2. **`db009_checker_run_final.py` 用了进程内 dmPython 桩**，它**只为绕过 import gate**，
   桩的 `connect()` 显式抛错、**不提供任何假连接**；真连接一律走平台连接器
   （pyodbc）。官方驱动下的真连接复验是 `db009_dm_sql_audit_dmpython.py`。
