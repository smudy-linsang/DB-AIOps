"""DB-AIOps 单一版本源。

本文件是**唯一**的版本号来源：文档（README.md / PROJECT_SUMMARY.md）的版本声明
由 scripts/check_doc_version.py 在 CI 中机械校验，禁止漂移。

版本历史：
  2.5.0  银行级治理升级 + 独立复审整改
  2.6.0  加固与治理（V2.6_HARDENING_DESIGN.md）：
         W1 数据范围+权限双闸闭合（修复第五次复发越权）
         W2 外部依赖功能开关落地（unit 层恢复 ~10s 契约）
         W3 RCA 3.0 因果链接入诊断管道（WarRoom 不再有页面无数据）
         W4 RCA 引擎统一 + 死代码清理
         W5 文档版本一致性机械校验
"""

__version__ = '2.6.0'

