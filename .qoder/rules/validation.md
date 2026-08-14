---
trigger: always_on
---

# 代码编辑后验证规则

## 强制：每次代码编辑后立即验证

在 db-aiops 项目中完成任何代码编辑（Write、SearchReplace 等文件修改操作）后，必须立即运行：

    cd db-aiops && scripts/validate.sh unit --json

- 退出码 0 → 验证通过，可继续下一步操作
- 退出码非 0 → 验证失败，必须先修复失败项并重新验证
- 验证未通过时禁止提交代码、禁止继续其他编辑

读取 JSON 输出的 `exit_code` 与首个 `status=="fail"` 的 `name` 定位失败项。

## 验证层级

| 场景 | 命令 | 耗时 |
|------|------|------|
| 每次代码编辑后（强制） | `scripts/validate.sh unit --json` | ~10s，零外部依赖 |
| 提交前（后端改动） | `scripts/validate.sh backend` | ~1min，需 PostgreSQL |
| 提交前（前端改动） | `scripts/validate.sh frontend` | ~30s |
| 两侧都改 | `scripts/validate.sh all` | ~2min |

## pre-commit 钩子（辅助安全网）

`scripts/validate.sh unit` 已接入 pre-commit 钩子（`scripts/pre-commit`），提交 Python 文件时自动执行。
安装钩子：`bash scripts/install-hooks.sh`。
此钩子仅作为辅助安全网 —— 主要依赖编辑后验证，不替代它。

CI（.github/workflows/ci.yml）是事后信号，不是合并门禁。本地验证是唯一的闸。

## 其他约束

- 提交信息格式：`feat(scope): 中文描述` / `fix(scope): ...` / `docs(scope): ...`
- 完成改动后自行 commit 并 push 到 origin master
- 提交前确认不含 .env、密钥等敏感信息（scripts/scan_secrets.py 会拦截）
- 新增配置项必须登记到 monitor/appconf.py 的 SPECS
- 捕获异常后按 monitor/degrade.py 分级留痕，不得完全静默
