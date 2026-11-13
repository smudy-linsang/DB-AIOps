-- =============================================================================
-- DB-026 b1 治本回退 SQL：撤销两个非 superuser 角色及其授权
-- =============================================================================
-- 用途：b1 实施面出现异常时的回退件（订单任务 2「备回退」）。
-- 执行时点：与实施同批授权范围内；D2 之前不执行（今天不执行任何写库动作）。
-- 注意：① 先 REASSIGN/DROP OWNED 再 DROP ROLE，否则角色仍持有对象或授权时 DROP 会失败；
--       ② 本脚本**不动**任何既有表/序列/数据，只撤销本次新建角色与其权限；
--       ③ 平台侧与 DB_Monitor 侧若已切到该角色，回退后须把连接串切回 postgres
--          并重启对应进程（重启属独立动作，按 D2 后窗口执行）。
-- =============================================================================

\set ON_ERROR_STOP on

\echo '=== 回退：撤销角色权限并删除角色 ==='
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbmonitor_app') THEN
    EXECUTE 'REASSIGN OWNED BY dbmonitor_app TO postgres';
    EXECUTE 'DROP OWNED BY dbmonitor_app';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbaiops_app') THEN
    EXECUTE 'REASSIGN OWNED BY dbaiops_app TO postgres';
    EXECUTE 'DROP OWNED BY dbaiops_app';
  END IF;
END $$;

DROP ROLE IF EXISTS dbmonitor_app;
DROP ROLE IF EXISTS dbaiops_app;

\echo '=== 回退后校验：两角色应不存在 ==='
SELECT count(*) AS remaining_roles
FROM pg_roles WHERE rolname IN ('dbmonitor_app', 'dbaiops_app');
