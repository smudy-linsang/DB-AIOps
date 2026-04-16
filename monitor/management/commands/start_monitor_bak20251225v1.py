from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from monitor.models import DatabaseConfig, MonitorLog
from django.db import connection
from apscheduler.schedulers.blocking import BlockingScheduler
import pymysql
import oracledb
import json
import datetime
import time

# === 新增：引入 PostgreSQL 驱动 ===
import psycopg2 

class Command(BaseCommand):
    help = '启动数据库监控守护进程 (带邮件告警)'

    def handle(self, *args, **options):
        """
        程序的入口函数 (相当于 main)
        """
        print(f"[{datetime.datetime.now()}] 🛡️ 监控守护进程已启动 (邮件告警模块加载完毕)...")
        print(">> 支持数据库: MySQL, Oracle, PostgreSQL")
        print(">> 按 Ctrl + C 可以停止运行")
        
        # 创建调度器 (定时任务管理器)
        scheduler = BlockingScheduler()
        
        # 添加任务：每 60 秒执行一次 self.monitor_job 函数
        scheduler.add_job(self.monitor_job, 'interval', seconds=60)
        
        # 第一次启动时先立即跑一次，不用傻等60秒
        self.monitor_job()
        
        try:
            # 开始阻塞运行，直到手动停止
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("\n监控进程已停止。")

    def monitor_job(self):
        """
        这是真正干活的函数，会被调度器定时调用
        """
        print(f"\n[{datetime.datetime.now()}] --- 开始新一轮巡检 ---")
        
        # DBA技巧: 防止长时间运行后，Django 自身的数据库连接断开
        connection.close_if_unusable_or_obsolete()
        
        # 1. 从数据库取出所有“开启监控”的配置
        configs = DatabaseConfig.objects.filter(is_active=True)
        
        for config in configs:
            # 2. 路由分发：根据数据库类型，调用不同的检查函数
            if config.db_type == 'mysql':
                self.check_mysql(config)
            elif config.db_type == 'oracle':
                self.check_oracle(config)
            elif config.db_type == 'pgsql': # <--- 新增 PG 分支
                self.check_pgsql(config)
            else:
                print(f"  -- 跳过暂不支持的类型: {config.name} ({config.db_type})")

    def check_mysql(self, config):
        """ MySQL 检查逻辑 """
        status = 'DOWN'
        result_data = {}
        conn = None
        try:
            # 建立连接
            conn = pymysql.connect(
                host=config.host, port=config.port, user=config.username, password=config.password,
                connect_timeout=5
            )
            cursor = conn.cursor()
            
            # --- 采集指标 ---
            cursor.execute("SELECT VERSION();")
            version = cursor.fetchone()[0]
            
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected';") # 当前连接数
            threads = cursor.fetchone()[1]
            
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime';") # 运行时间(秒)
            uptime = cursor.fetchone()[1]
            
            status = 'UP'
            result_data = {"version": version, "active_connections": threads, "uptime_seconds": uptime}
            print(f"  √ MySQL [{config.name}]: 正常")
            
        except Exception as e:
            result_data = {"error": str(e)}
            print(f"  X MySQL [{config.name}]: 失败 - {e}")
        finally:
            if conn: conn.close()

        # 统一处理结果(保存+告警)
        self.process_result(config, status, result_data)

    def check_oracle(self, config):
        """ Oracle 检查逻辑 """
        status = 'DOWN'
        result_data = {}
        conn = None
        try:
            # 优先使用配置的服务名，没填则默认 orcl
            target_service = config.service_name if config.service_name else 'orcl'
            
            # 建立连接
            conn = oracledb.connect(
                user=config.username, password=config.password,
                host=config.host, port=config.port, service_name=target_service
            )
            cursor = conn.cursor()
            
            # --- 采集指标 (v$视图) ---
            cursor.execute("SELECT BANNER FROM v$version WHERE ROWNUM = 1")
            version = cursor.fetchone()[0]
            
            cursor.execute("SELECT count(*) FROM v$session") # 当前会话数
            sessions = cursor.fetchone()[0]
            
            # 计算启动时长: 当前时间 - 启动时间
            cursor.execute("SELECT (SYSDATE - startup_time) * 24 * 60 * 60 FROM v$instance")
            uptime = int(cursor.fetchone()[0])

            status = 'UP'
            result_data = {
                "version": version[:50]+"...", # 截取一下防止太长
                "active_connections": sessions, 
                "uptime_seconds": uptime
            }
            print(f"  √ Oracle [{config.name}]: 正常")
            
        except Exception as e:
            result_data = {"error": str(e)}
            print(f"  X Oracle [{config.name}]: 失败 - {e}")
        finally:
            if conn: conn.close()
            
        self.process_result(config, status, result_data)

    def check_pgsql(self, config):
        """ 
        [新增] PostgreSQL 检查逻辑 
        """
        status = 'DOWN'
        result_data = {}
        conn = None
        try:
            # 建立连接 (PG 默认连 postgres 库，除非指定)
            # 注意: config.service_name 在 PG 里我们用来当 'database name' 用
            target_db = config.service_name if config.service_name else 'postgres'
            
            conn = psycopg2.connect(
                database=target_db,
                user=config.username,
                password=config.password,
                host=config.host,
                port=config.port,
                connect_timeout=5
            )
            cursor = conn.cursor()
            
            # --- 采集指标 ---
            # 1. 获取版本
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            
            # 2. 获取活跃连接数 (相当于 v$session)
            # pg_stat_activity 是 PG 最重要的监控视图
            cursor.execute("SELECT count(*) FROM pg_stat_activity;")
            connections = cursor.fetchone()[0]
            
            # 3. 获取运行时间 (当前时间 - 主进程启动时间)
            cursor.execute("SELECT extract(epoch from (now() - pg_postmaster_start_time()));")
            uptime = int(cursor.fetchone()[0])
            
            status = 'UP'
            result_data = {
                "version": version[:50] + "...", 
                "active_connections": connections,
                "uptime_seconds": uptime
            }
            print(f"  √ PGSQL [{config.name}]: 正常")
            
        except Exception as e:
            result_data = {"error": str(e)}
            print(f"  X PGSQL [{config.name}]: 失败 - {e}")
        finally:
            if conn: conn.close()
            
        self.process_result(config, status, result_data)

    def process_result(self, config, current_status, data):
        """
        统一处理函数：保存日志 + 状态比对 + 发送告警
        """
        # 1. 查出【上一次】的记录，用于比对状态
        last_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
        
        # 2. 告警逻辑
        # 情况A: 之前是 UP，现在变成 DOWN -> 报故障
        if last_log and last_log.status == 'UP' and current_status == 'DOWN':
            self.send_alert_email(config, '🔴 故障告警', data.get('error', '未知错误'))
            
        # 情况B: 之前是 DOWN，现在变成 UP -> 报恢复
        elif last_log and last_log.status == 'DOWN' and current_status == 'UP':
            self.send_alert_email(config, '🟢 恢复通知', '数据库已重新连接')

        # 3. 保存本次巡检结果到数据库
        MonitorLog.objects.create(
            config=config,
            status=current_status,
            message=json.dumps(data, ensure_ascii=False)
        )

    def send_alert_email(self, config, title_prefix, error_msg):
        """
        发送邮件的通用函数
        """
        subject = f"{title_prefix}: {config.name} ({config.host})"
        message = (
            f"数据库监控通知\n"
            f"--------------------------------\n"
            f"数据库: {config.name}\n"
            f"地址: {config.host}:{config.port}\n"
            f"类型: {config.get_db_type_display()}\n"
            f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"详情: {error_msg}\n"
            f"--------------------------------\n"
            f"请尽快检查！"
        )
        
        print(f"  >>> 正在触发邮件告警: {subject} ...")
        
        try:
            # 使用 Django 配置文件(settings.py)里的邮箱发送
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                settings.ADMIN_EMAILS, 
                fail_silently=False,
            )
            print("  >>> 邮件发送成功！")
        except Exception as e:
            print(f"  >>> 邮件发送失败: {e}")