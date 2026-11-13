-- =============================================================================
-- DB-026 b1 治本实施 SQL：建非 superuser 角色 + 授权
-- =============================================================================
-- 用途：DB-026 订单任务 2 的「D2 之前只做评估与准备（写配置、备脚本、备回退）」，
--       本脚本即 b1-1（建角色）与 b1-2（授权）的执行件。
-- 授权依据：STATUS v1.47（commit 53a2900）—— G-5 获准（限 b1 的 S3）、S-2 采纳
--       （平台侧同设 CONNECTION LIMIT、与 b1 同批）、S-3 采纳（授权须覆盖 dev 副本 migrate）。
-- 执行时点：**一律在 11-15 09:50 D2 复判之后**；D2 之前只评估、不动库。
-- 口令纪律：口令**不写入本文件、不进仓库**。须由执行人经 psql 变量注入：
--       psql -v pw="$env:DB026_ROLE_PASSWORD" -v pw2="$env:DB026_APP_ROLE_PASSWORD" -f ...
--       （-v 传值会在宿主进程列表中短暂可见；若环境要求更严，改由操作者在容器内
--        交互式执行本文件并自行 set 变量。）
-- 幂等性：建角色走 \gexec 形式（存在即跳过建、并用 ALTER 对齐参数与口令）；
--        授权语句本身幂等，可重复执行。
-- 角色与限额：
--   dbmonitor_app —— 给 DB_Monitor 开发副本（b1-3）：CONNECTION LIMIT 30
--   dbaiops_app  —— 给本平台自身（b1-5，S-2 平台侧同批）：CONNECTION LIMIT 60
--   ※ 两限额合计 90 < 可用槽位 97（max_connections 100 − superuser_reserved 3），
--     故两角色同时封顶时仍有 7 个余量，不会互斥。
--   ※ 平台侧 60 系按「时序池上限 16 ＋ ORM 预留」估的**实施参数**，实施时可调，
--     调整前须复核不与 DB_Monitor 侧 30 相加超出 97。
-- =============================================================================

\set ON_ERROR_STOP on

-- 守卫：口令变量缺失时直接报错退出（ON_ERROR_STOP=1 ⇒ psql 非零退出，且此前无任何写库动作）
\if :{?pw}
\else
  DO $$ BEGIN RAISE EXCEPTION '缺少变量 pw：请用 -v pw=... 注入 DB_Monitor 副本角色口令（不要写进文件或仓库）'; END $$;
\endif
\if :{?pw2}
\else
  DO $$ BEGIN RAISE EXCEPTION '缺少变量 pw2：请用 -v pw2=... 注入平台侧角色口令（不要写进文件或仓库）'; END $$;
\endif

\echo '=== [1/4] 建角色（存在则跳过建、对齐参数与口令）==='

-- 关键前提：PostgreSQL 的 rolconnlimit **对 superuser 不生效**。
-- 因此「设限额」必须与「把应用切到该角色」同批完成，否则限额对应用自身即空话。
SELECT format(
  'CREATE ROLE dbmonitor_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT CONNECTION LIMIT 30 PASSWORD %L',
  :'pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbmonitor_app')
\gexec

SELECT format(
  'ALTER ROLE dbmonitor_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT CONNECTION LIMIT 30 PASSWORD %L',
  :'pw')
\gexec

SELECT format(
  'CREATE ROLE dbaiops_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT CONNECTION LIMIT 60 PASSWORD %L',
  :'pw2')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbaiops_app')
\gexec

SELECT format(
  'ALTER ROLE dbaiops_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT CONNECTION LIMIT 60 PASSWORD %L',
  :'pw2')
\gexec

\echo '=== [2/4] 库级 CONNECT 授权 ==='
GRANT CONNECT ON DATABASE db_monitor TO dbmonitor_app, dbaiops_app;
GRANT CONNECT ON DATABASE timeseriesdb TO dbmonitor_app, dbaiops_app;

\echo '=== [3/4] schema 级授权（public：USAGE+CREATE，覆盖 dev 副本 migrate 建表能力）==='

\connect db_monitor
GRANT USAGE, CREATE ON SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT ALL PRIVILEGES ON ALL VIEWS IN SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO dbmonitor_app, dbaiops_app;

\connect timeseriesdb
GRANT USAGE, CREATE ON SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT ALL PRIVILEGES ON ALL VIEWS IN SCHEMA public TO dbmonitor_app, dbaiops_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO dbmonitor_app, dbaiops_app;

-- TimescaleDB 内部 schema：超表/会话采样等扩展对象在 hypertable 内部目录，
-- 非 superuser 角色访问 hypertable 需此权限。
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '_timescaledb_internal') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA _timescaledb_internal TO dbmonitor_app, dbaiops_app';
  END IF;
END $$;

\echo '=== [4/4] 实施后校验 ==='
SELECT rolname, rolsuper, rolcanlogin, rolconnlimit, rolcreatedb
FROM pg_roles WHERE rolname IN ('dbmonitor_app', 'dbaiops_app') ORDER BY rolname;

-- ★残余项（G-3 授权完整性）：TimescaleDB 的 create_hypertable / add_retention_policy /
--   add_compression_policy 需要**对象属主**权限，非 superuser 角色对 postgres 拥有的
--   超表无法执行 ⇒ dev 副本首次建超表须由 postgres 执行一次，或按需把目标对象属主转给
--   该角色。以下查询列出仍由 postgres 拥有的表，供实施时逐项判定。
SELECT table_schema, count(*) AS still_owned_by_postgres
FROM pg_tables WHERE tableowner = 'postgres' GROUP BY 1 ORDER BY 1;
