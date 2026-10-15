"""集成层测试配置：**真实 PostgreSQL + 零外部依赖**。

## 为什么需要它

集成层用例跑在真实 PostgreSQL 上（`settings_test_unit` 那种 SQLite 内存库没有
行锁语义，放进去只会得到假绿），但**并不需要** Elasticsearch、TimescaleDB、
Redis、LLM、真实 SMTP 这些外部服务。全量套件里大量用例会触碰它们，而"服务
不可用"本身要被断言，于是每次触碰都变成一次真实的连接失败。

代价在 Windows 上尤其贵：`localhost` 先解析 `::1` 再回落 IPv4，每次对
`localhost:9200` 的失败连接约耗 4 秒，叠加自监控/告警类用例的反复重试，
全量后端闸门要跑 **约 30 分钟**（实测 443 条 / 1825.9 秒）。Linux 上同一个失败
是瞬时的，所以 CI 只要 2 分 50 秒 —— 这说明它不是产品缺陷，而是环境耦合。

## 30 分钟的闸门等于没有闸门

`AGENTS.md` 要求"改动后立即跑闸门"，但没人会在提交前等 30 分钟。这条配置把
外部依赖关掉，让全量回到分钟级，让"提交前跑全量"从一句空话变成可执行动作。

## 与 unit 层的分工

| | 库 | 外部依赖 | 用途 |
|---|---|---|---|
| `settings_test_unit` | SQLite 内存 | 全关 | 纯逻辑，秒级 |
| **`settings_test_integration`** | **真实 PostgreSQL** | **全关** | **全量，分钟级** |
| `settings`（默认） | 真实 PostgreSQL | 真实服务 | 开发/生产手工运行 |

## 边界：关闭开关不等于降低覆盖

被关掉的只是**外部服务的连接**，不是**被测代码的分支**。"ES 不可用时如何降级"
这类断言依然成立 —— 走的正是 `ES_ENABLED=False` 那条降级路径，与生产上 ES
宕机时的行为完全一致。真正需要活服务的验收（方言实库矩阵、容量、长稳）不在
本套件内，另行安排（见 `V3.0_SIT_UAT_TEST_REPORT.md` 遗留项 R-5）。

CI 的 `test-integration` job 仍跑默认 settings，作为"真实配置"的独立基线 ——
两道闸门覆盖不同配置面，互为补充。
"""
from .settings import *  # noqa: F401,F403

# 库保持 settings 中的真实 PostgreSQL 配置（需要真实行锁/约束语义）。
# 这里只关外部服务，不碰 DATABASES。

# ---- 外部服务：一律关闭 ----
TIMESCALEDB_ENABLED = False
ES_ENABLED = False
LLM_ENABLED = False
AGENT_ENABLED = False
EMBED_ENABLED = False
USE_REDIS_CACHE = False

# settings 里 EMAIL_BACKEND 是硬编码 SMTP 后端（见 settings.py），
# 集成用例里任何一次发信都会真的去连 SMTP 服务器。换成内存后端。
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']  # 仅测试提速

# 刻意**不**覆盖的项（保持与 CI 默认一致，避免本地与 CI 行为漂移）：
#   AUTH_ALLOW_COOKIE_TOKEN / READINESS_REQUIRE_WORKERS / LOGGING_CONFIG
