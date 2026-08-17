# v2.5 独立复审：缺陷复现用例

配套文档：仓库根目录 `V2.5_INDEPENDENT_REVIEW.md`

## 这里放的是什么

`tests_v25_review.py` 里的 6 条用例，每条复现该报告中的一个问题。
它们**当前会失败**，这是预期的 —— 失败即证明缺陷仍在。

## 为什么不放在 `monitor/` 下

放进去会被 Django 自动发现，整个测试套件立刻变红。本仓库直推 master、
CI 是事后信号，红了就是真红，长期挂红会让红灯失去意义。

上一轮（v2.0）我用 `@unittest.expectedFailure` 解决了同样的处境。
但 v2.5 已在 `monitor/tests_v2_review.py` 头部立下规矩：

> 新增缺陷不得以 expected failure 方式换取绿灯。

这条规矩是对的，所以本轮不再用那个办法。

## 代价（必须说清楚）

放在这里意味着**没有任何自动化会执行它们** —— 这正是复审报告 REV-17 批评过的
`tests_phase7.py` 那种"断言存在但没人跑"的状态。

**因此这是一个临时位置，不是归宿。** 修复某一项时，请把对应用例移回
`monitor/tests_v25_review.py`，它就从"缺陷复现"转正为"回归防线"。
全部修完时，本目录应当为空。

## 手动运行

```bash
cp docs/review/v2.5/tests_v25_review.py monitor/
DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit \
  python manage.py test monitor.tests_v25_review -v 2
rm monitor/tests_v25_review.py     # 跑完记得移走，否则套件会红
```

## 当前结果（2026-08-17，master 99cfcd9）

```
FAIL: test_inventory_must_not_leak_out_of_scope_databases   R25-01 越权泄露
FAIL: test_ash_tool_must_not_invent_sessions                R25-02 编造 ASH
FAIL: test_tablespace_tool_must_not_invent_high_watermark   R25-02 编造表空间
FAIL: test_explain_tool_must_not_invent_execution_plan      R25-02 编造执行计划
FAIL: test_tablespace_tool_respects_selected_database       R25-04 config 未赋值
FAIL: test_api_key_must_not_be_stored_in_plaintext          R25-03 凭据明文

Ran 6 tests — FAILED (failures=6)
```
