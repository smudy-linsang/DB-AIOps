# v2.5 整改复测：新发现问题的复现用例

配套文档：仓库根目录 `V2.5_REMEDIATION_RETEST.md`

`tests_retest_scope.py` 复现 RT-01：`/api/v1/capacity/overview/` 与
`/api/v1/topology/overview/` 只有 `require_auth`，既无 `require_permission`
也无数据范围过滤，零权限账号即可读到全部实例的名称与主机地址。

这两处在 `monitor/api_views.py`，**本次整改未触碰，属既有缺陷**。

## 为什么放在这里而不是 monitor/

放进去会被 Django 自动发现，master 测试套件立刻变红。
v2.5 已立规矩"新增缺陷不得以 expected failure 换取绿灯"，所以也不用那个办法。

**这是临时位置。** 修复后请把文件移回 `monitor/`，它即转正为回归防线；
本目录届时应为空。

## 手动运行

```bash
cp docs/review/retest/tests_retest_scope.py monitor/
DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit \
  python manage.py test monitor.tests_retest_scope -v 2
rm monitor/tests_retest_scope.py
```
