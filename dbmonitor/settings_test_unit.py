"""unit 层测试配置：零外部依赖。

存在的意义（对应诊断 D2/A2）：完整测试需要 PostgreSQL 容器，
门槛一高，编辑循环里就没人跑了 —— 这正是"93 次编辑 0 次验证"的成因。
本配置让纯逻辑测试在 10 秒内跑完，且不需要任何 Docker。
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
# 外部服务一律关闭，避免用例误触真实依赖
TIMESCALEDB_ENABLED = False
ES_ENABLED = False
LLM_ENABLED = False
AGENT_ENABLED = False
EMBED_ENABLED = False
AUTH_ALLOW_COOKIE_TOKEN = False
READINESS_REQUIRE_WORKERS = False
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']  # 仅测试提速
LOGGING_CONFIG = None   # 保留测试中的 assertLogs 行为
