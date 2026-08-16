---
trigger: always_on
---

<!-- 同步关系：本文件（db-aiops/.qoder/rules/validation.md，git 版本控制内）是权威源；
     工作区根副本 /Users/mac/DB_Monitor/.qoder/rules/validation.md 供平台规则发现，
     修改本文件后必须将全文同步覆盖到工作区根副本，禁止只改其中一份。 -->

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

## 编辑时刻 Qoder Hook（确定性强制层）

`scripts/validate.sh unit --json` 已接入 Qoder 编辑时刻触发面：工作区根
`.qoder/settings.json` 注册 `PostToolUse` 钩子（matcher `Write|Edit|SearchReplace`），
脚本为工作区根 `.qoder/hooks/post-edit-validate.sh`。db-aiops 内文件被编辑后钩子自动运行 unit 验证：

- validate.sh 退出码 0 → 钩子放行（exit 0）
- validate.sh 退出码 1 → 钩子以 exit 2 阻断，stderr 注入会话并给出首个失败阶段名与修复指引
- validate.sh 退出码 2 → 上报环境/用法错误，不阻断
- 每次运行留痕于 `db-aiops/logs/qoder-edit-validate.log`（已被 .gitignore 忽略）

钩子不改变 validate.sh 的分层结构与退出码语义，只做触发面接入；
钩子配置在 Qoder 启动加载后生效（通常需新会话）。pre-commit 仍是提交时刻辅助安全网。

## pre-commit 钩子（辅助安全网）

`scripts/validate.sh unit` 已接入 pre-commit 钩子（`scripts/pre-commit`），提交 Python 文件时自动执行。
安装钩子：`bash scripts/install-hooks.sh`。
此钩子仅作为辅助安全网 —— 主要依赖编辑后验证，不替代它。

## CI 性质（事后信号，非门禁）

> 权威描述以 `AGENTS.md` 的「协作约定」章节为准（理由见
> `PROJECT_IMPROVEMENT_DESIGN.md` 附录 B.6.2），本节只保留精简入口。

CI（`.github/workflows/ci.yml`）是事后信号，不是合并门禁。本地验证是唯一的闸：

- 本仓库直推 master，没有 ruleset、没有必需状态检查，推前没有任何机制拦截；
- CI 在 push 之后才跑（静态检查、单元、集成、方言 MySQL/PG、方言 Oracle、
  安全扫描、前端，共 7 项）；推之前请自己跑 `scripts/validate.sh`，
  改到采集/方言相关代码时尽量连方言测试一起跑；
- master 挂红就是真红 —— Oracle job 已摘掉 `continue-on-error`，且有
  「确认用例真的跑了」守住绿灯含义，不存在「红了也没关系」的 job。
  推完回头看一眼 CI，看到红请立刻修或回滚，别留给下一个人。

> 本规则的 `trigger: always_on` 仅约束本地编辑后验证的强制性，
> 不改变 CI 的事后信号性质——CI 结果不会在推送前拦住你。

## 其他约束

- 提交信息格式：`feat(scope): 中文描述` / `fix(scope): ...` / `docs(scope): ...`
- 完成改动后自行 commit 并 push 到 origin master
- 提交前确认不含 .env、密钥等敏感信息（scripts/scan_secrets.py 会拦截）
- 新增配置项必须登记到 monitor/appconf.py 的 SPECS
- 捕获异常后按 monitor/degrade.py 分级留痕，不得完全静默
