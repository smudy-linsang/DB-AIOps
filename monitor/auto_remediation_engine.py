"""
自动化处理引擎 v1.0 (Auto Remediation)

功能:
- 根据 RCA 诊断结果自动生成修复命令
- 支持一键执行（需人工确认）
- 操作审计日志
- 回滚建议生成
"""

import json
import datetime
from django.utils import timezone
from django.db import models
from monitor.models import DatabaseConfig, MonitorLog, AuditLog


# 自动化处理引擎
# ==========================================
class AutoRemediationEngine:
    """自动化处理引擎"""
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.db_type = config.db_type
    
    def generate_remediation_plan(self, diagnosis):
        """
        根据诊断结果生成修复方案
        
        参数:
            diagnosis: RCA 引擎的诊断结果
        
        返回:
            {
                'action_type': 操作类型,
                'description': 操作描述,
                'sql_command': SQL 命令,
                'risk_level': 风险等级,
                'rollback_command': 回滚命令,
                'prerequisites': 前置条件,
                'estimated_impact': 预期影响
            }
        """
        rule_id = diagnosis.get('rule_id')
        
        if rule_id == 'R002':  # 锁等待
            return self._plan_kill_session(diagnosis)
        elif rule_id == 'R003':  # 表空间不足
            return self._plan_resize_datafile(diagnosis)
        elif rule_id == 'R001':  # 连接数泄漏
            return self._plan_cleanup_sessions(diagnosis)
        else:
            return None
    
    def _plan_kill_session(self, diagnosis):
        """生成终止会话的方案"""
        db_type = self.db_type
        
        if db_type == 'oracle':
            return {
                'action_type': 'KILL_SESSION',
                'description': '终止造成阻塞的数据库会话',
                'sql_command': self._get_oracle_kill_sql(),
                'risk_level': 'high',
                'rollback_command': '-- Kill 操作不可回滚，需应用程序重新连接',
                'prerequisites': [
                    '确认阻塞会话的 SID 和 SERIAL#',
                    '确认该会话不是关键业务会话',
                    '通知应用程序负责人'
                ],
                'estimated_impact': '被终止的会话将立即断开，正在进行的事务会回滚'
            }
        
        elif db_type in ['mysql', 'tdsql']:
            return {
                'action_type': 'KILL_SESSION',
                'description': '终止造成阻塞的数据库线程',
                'sql_command': self._get_mysql_kill_sql(),
                'risk_level': 'high',
                'rollback_command': '-- Kill 操作不可回滚，需应用程序重新连接',
                'prerequisites': [
                    '确认阻塞线程的 THREAD_ID',
                    '确认该线程不是关键业务线程',
                    '通知应用程序负责人'
                ],
                'estimated_impact': '被终止的线程将立即断开，正在进行的事务会回滚'
            }
        
        elif db_type == 'pgsql':
            return {
                'action_type': 'KILL_SESSION',
                'description': '终止造成阻塞的数据库进程',
                'sql_command': self._get_pg_kill_sql(),
                'risk_level': 'high',
                'rollback_command': '-- Terminate 操作不可回滚，需应用程序重新连接',
                'prerequisites': [
                    '确认阻塞进程的 PID',
                    '确认该进程不是关键业务进程',
                    '通知应用程序负责人'
                ],
                'estimated_impact': '被终止的进程将立即断开，正在进行的事务会回滚'
            }
        
        elif db_type == 'dm':
            return {
                'action_type': 'KILL_SESSION',
                'description': '终止造成阻塞的达梦会话',
                'sql_command': self._get_dm_kill_sql(),
                'risk_level': 'high',
                'rollback_command': '-- Kill 操作不可回滚，需应用程序重新连接',
                'prerequisites': [
                    '确认阻塞会话的 SID',
                    '确认该会话不是关键业务会话',
                    '通知应用程序负责人'
                ],
                'estimated_impact': '被终止的会话将立即断开，正在进行的事务会回滚'
            }
        
        return None
    
    def _plan_resize_datafile(self, diagnosis):
        """生成扩容数据文件的方案"""
        db_type = self.db_type
        
        if db_type == 'oracle':
            return {
                'action_type': 'RESIZE_DATAFILE',
                'description': '扩容 Oracle 数据文件',
                'sql_command': self._get_oracle_resize_sql(),
                'risk_level': 'medium',
                'rollback_command': "-- 回滚示例:\nALTER DATABASE DATAFILE '/path/to/datafile.dbf' RESIZE 5G;",
                'prerequisites': [
                    '确认磁盘空间充足',
                    '确认数据文件路径正确',
                    '建议在业务低峰期执行'
                ],
                'estimated_impact': '扩容过程中该表空间可正常读写，无感知'
            }
        
        elif db_type in ['mysql', 'tdsql']:
            return {
                'action_type': 'ADD_DATAFILE',
                'description': '为 MySQL 表空间添加数据文件',
                'sql_command': self._get_mysql_add_datafile_sql(),
                'risk_level': 'medium',
                'rollback_command': "-- 回滚示例:\nALTER TABLESPACE <tablespace_name> DROP DATAFILE '/path/to/newfile.ibd';",
                'prerequisites': [
                    '确认磁盘空间充足',
                    '确认表空间名称正确',
                    '建议在业务低峰期执行'
                ],
                'estimated_impact': '添加数据文件过程中表空间可正常读写，无感知'
            }
        
        return None
    
    def _plan_cleanup_sessions(self, diagnosis):
        """生成清理空闲会话的方案"""
        return {
            'action_type': 'KILL_SESSION',
            'description': '清理长时间空闲的数据库会话',
            'sql_command': self._get_cleanup_idle_sessions_sql(),
            'risk_level': 'medium',
            'rollback_command': '-- 清理操作不可回滚，被清理的会话需应用程序重新连接',
            'prerequisites': [
                '确认空闲会话列表',
                '排除关键业务会话',
                '通知应用程序负责人'
            ],
            'estimated_impact': '被清理的空闲会话将断开，可能释放连接数资源'
        }
    
    # ========== 各数据库类型的 SQL 模板 ==========
    
    def _get_oracle_kill_sql(self):
        return """-- Oracle Kill Session 模板
-- 请替换 <SID> 和 <SERIAL#> 为实际值
ALTER SYSTEM KILL SESSION '<SID>,<SERIAL#>' IMMEDIATE;

-- 如果需要从 gv$session 查找阻塞会话:
SELECT 
    'ALTER SYSTEM KILL SESSION ''' || sid || ',' || serial# || ''' IMMEDIATE;' as kill_cmd
FROM gv$session 
WHERE blocking_session IS NOT NULL;"""
    
    def _get_mysql_kill_sql(self):
        return """-- MySQL Kill Thread 模板
-- 请替换 <THREAD_ID> 为实际值
KILL <THREAD_ID>;

-- 如果需要从 information_schema 查找阻塞线程:
SELECT 
    CONCAT('KILL ', blocking_trx.trx_mysql_thread_id, ';') as kill_cmd
FROM information_schema.innodb_lock_waits w
INNER JOIN information_schema.innodb_trx blocking_trx ON w.blocking_trx_id = blocking_trx.trx_id;"""
    
    def _get_pg_kill_sql(self):
        return """-- PostgreSQL Terminate Backend 模板
-- 请替换 <PID> 为实际值
SELECT pg_terminate_backend(<PID>);

-- 如果需要从 pg_locks 查找阻塞进程:
SELECT 
    'SELECT pg_terminate_backend(' || blocked_locks.pid || ');' as kill_cmd
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
WHERE NOT blocked_locks.GRANTED;"""
    
    def _get_dm_kill_sql(self):
        return """-- 达梦 Kill Session 模板
-- 请替换 <SID> 为实际值
ALTER SYSTEM KILL SESSION <SID>;

-- 如果需要从 v$sessions 查找阻塞会话:
SELECT 
    'ALTER SYSTEM KILL SESSION ' || SID || ';' as kill_cmd
FROM v$sessions 
WHERE SID IN (SELECT SID FROM v$lock WHERE BLOCK=1);"""
    
    def _get_oracle_resize_sql(self):
        return """-- Oracle Resize Datafile 模板
-- 请替换文件路径和目标大小
ALTER DATABASE DATAFILE '/u01/app/oracle/oradata/ORCL/system01.dbf' RESIZE 10G;

-- 查看当前数据文件大小:
SELECT file_name, bytes/1024/1024 as size_mb 
FROM dba_data_files 
WHERE tablespace_name = '<TABLESPACE_NAME>';"""
    
    def _get_mysql_add_datafile_sql(self):
        return """-- MySQL Add Datafile 模板 (InnoDB)
-- 请替换表空间名称和文件路径
ALTER TABLESPACE <tablespace_name> ADD DATAFILE '/var/lib/mysql/newfile.ibd' SIZE 10G ENGINE=INNODB;

-- 或者为特定表增加空间:
ALTER TABLE <table_name> AUTO_INCREMENT = 1; -- 重置自增 ID（谨慎使用）"""
    
    def _get_cleanup_idle_sessions_sql(self):
        if self.db_type == 'oracle':
            return """-- Oracle 清理空闲会话
SELECT 
    'ALTER SYSTEM KILL SESSION ''' || sid || ',' || serial# || ''' IMMEDIATE;' as kill_cmd
FROM v$session 
WHERE status = 'ACTIVE' 
  AND last_call_et > 3600  -- 空闲超过 1 小时
  AND username IS NOT NULL;"""
        elif self.db_type in ['mysql', 'tdsql']:
            return """-- MySQL 清理空闲会话
SELECT 
    CONCAT('KILL ', id, ';') as kill_cmd
FROM information_schema.processlist
WHERE command = 'Sleep' 
  AND time > 3600  -- 空闲超过 1 小时
  AND user != 'system';"""
        elif self.db_type == 'pgsql':
            return """-- PostgreSQL 清理空闲会话
SELECT 
    'SELECT pg_terminate_backend(' || pid || ');' as kill_cmd
FROM pg_stat_activity 
WHERE state = 'idle' 
  AND query_start < NOW() - INTERVAL '1 hour'
  AND pid != pg_backend_pid();"""
        
        return "-- 暂不支持该数据库类型的空闲会话清理"
    
    def create_audit_record(self, plan, related_log=None):
        """创建审计记录"""
        audit = AuditLog.objects.create(
            config=self.config,
            related_log=related_log,
            action_type=plan['action_type'],
            description=plan['description'],
            sql_command=plan['sql_command'],
            risk_level=plan['risk_level'],
            rollback_command=plan.get('rollback_command', ''),
            status='pending'
        )
        return audit
    
    def approve_operation(self, audit_id, approver):
        """批准操作"""
        try:
            audit = AuditLog.objects.get(id=audit_id)
            audit.status = 'approved'
            audit.approver = approver
            audit.approve_time = timezone.now()
            audit.save()
            return True, "操作已批准"
        except AuditLog.DoesNotExist:
            return False, "审计记录不存在"
    
    def reject_operation(self, audit_id, reason=''):
        """拒绝操作"""
        try:
            audit = AuditLog.objects.get(id=audit_id)
            audit.status = 'rejected'
            audit.execution_result = f"拒绝原因：{reason}"
            audit.save()
            return True, "操作已拒绝"
        except AuditLog.DoesNotExist:
            return False, "审计记录不存在"
    
    def execute_operation(self, audit_id, executor, db_connection):
        """
        执行操作
        
        参数:
            audit_id: 审计记录 ID
            executor: 执行人
            db_connection: 数据库连接对象
        
        返回:
            (success, message)
        """
        try:
            audit = AuditLog.objects.get(id=audit_id)
            
            if audit.status != 'approved':
                return False, "操作未批准，无法执行"
            
            # 更新状态
            audit.status = 'executing'
            audit.executor = executor
            audit.execute_time = timezone.now()
            audit.save()
            
            # 执行 SQL (这里只记录，不实际执行，实际执行需要数据库权限)
            cursor = db_connection.cursor()
            try:
                # 分割并执行多条 SQL
                sql_commands = audit.sql_command.split(';')
                results = []
                
                for sql in sql_commands:
                    sql = sql.strip()
                    if sql and not sql.startswith('--'):
                        cursor.execute(sql)
                        if cursor.description:  # 有返回值
                            result = cursor.fetchall()
                            results.append(result)
                
                audit.status = 'success'
                audit.execution_result = f"执行成功\n受影响行数：{cursor.rowcount}"
                audit.save()
                
                return True, "操作执行成功"
                
            except Exception as e:
                audit.status = 'failed'
                audit.execution_result = f"执行失败：{str(e)}"
                audit.save()
                
                return False, f"执行失败：{str(e)}"
            finally:
                cursor.close()
                
        except AuditLog.DoesNotExist:
            return False, "审计记录不存在"
        except Exception as e:
            return False, f"执行异常：{str(e)}"
    
    def get_pending_operations(self):
        """获取待执行的操作列表"""
        return AuditLog.objects.filter(
            config=self.config,
            status='pending'
        ).order_by('-create_time')
    
    def get_operation_history(self, limit=50):
        """获取操作历史"""
        return AuditLog.objects.filter(
            config=self.config
        ).order_by('-create_time')[:limit]


# ==========================================
# 使用示例

