from django.db import models
from django.utils import timezone

# 这里定义支持监控的数据库类型，作为下拉菜单选项
DB_TYPES = (
    ('oracle', 'Oracle'),
    ('mysql', 'MySQL'),
    ('redis', 'Redis'), # 预留
    ('pgsql', 'PostgreSQL'),
    ('mongo', 'MongoDB'),
    ('dm', '达梦数据库'),
    ('gbase', 'Gbase 8a'),
    ('tdsql', 'TDSQL'),
)

class DatabaseConfig(models.Model):
    # 相当于: name VARCHAR(100) NOT NULL COMMENT '连接别名'
    name = models.CharField(max_length=100, verbose_name="连接别名", help_text="例如: 核心交易库_主节点")
    
    # 相当于: db_type VARCHAR(20)
    db_type = models.CharField(max_length=20, choices=DB_TYPES, verbose_name="数据库类型")
    
    # 相当于: host VARCHAR(100)
    host = models.CharField(max_length=100, verbose_name="IP地址")
    
    # 相当于: port INT
    port = models.IntegerField(verbose_name="端口号")
    
    # 相当于: username VARCHAR(100)
    username = models.CharField(max_length=100, verbose_name="用户名")
    
    # 密码字段：v0.1.0 起支持 AES-256-GCM 加密存储（以 "enc:" 开头表示密文）
    # 旧数据（明文）仍兼容，通过 get_password() 统一读取
    password = models.CharField(max_length=512, verbose_name="密码")
    
    # === 新增字段 ===
    # blank=True, null=True 表示这个字段可以为空（因为MySQL不需要填这个）
    service_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="服务名/SID", help_text="Oracle必填服务名，其他库可留空")

    # 相当于: is_active BOOLEAN DEFAULT TRUE
    is_active = models.BooleanField(default=True, verbose_name="是否开启监控")

    # 创建时间
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    def get_password(self) -> str:
        """返回明文密码（自动解密已加密的密码）"""
        from monitor.crypto import decrypt_password
        return decrypt_password(self.password)

    def set_password(self, plaintext: str):
        """加密并保存密码（不自动 save，需调用者手动 save）"""
        from monitor.crypto import encrypt_password
        self.password = encrypt_password(plaintext)

    def __str__(self):
        return f"{self.name} ({self.host})"

    class Meta:
        verbose_name = "数据库配置"
        verbose_name_plural = "数据库配置列表"

class MonitorLog(models.Model):
    # 关联到具体的数据库配置 (外键)
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, verbose_name="数据库")
    
    # 监控状态
    status = models.CharField(max_length=10, default='UP', verbose_name="状态") # UP/DOWN
    
    # 具体的指标数据，我们存成文本格式 (JSON字符串)，这样不同数据库的不同指标都能存
    # 例如: {"version": "8.0", "connections": 50, "qps": 100}
    message = models.TextField(verbose_name="监控数据/报错信息")
    
    # 巡检时间
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="巡检时间")

    def __str__(self):
        return f"{self.config.name} - {self.create_time}"

    class Meta:
        verbose_name = "监控日志"
        verbose_name_plural = "监控日志列表"


# ==========================================
# 运维操作审计日志 (Phase 3 新增)
# ==========================================
class AuditLog(models.Model):
    """运维操作审计日志"""
    
    ACTION_CHOICES = (
        ('KILL_SESSION', '终止会话'),
        ('RESIZE_DATAFILE', '扩容数据文件'),
        ('ADD_DATAFILE', '添加数据文件'),
        ('DROP_INDEX', '删除索引'),
        ('PURGE_TABLE', '清理表数据'),
        ('REBALANCE_SHARD', '分片重平衡'),
        ('EXECUTE_SQL', '执行自定义 SQL'),
    )
    
    RISK_LEVEL_CHOICES = (
        ('low', '低风险'),
        ('medium', '中风险'),
        ('high', '高风险'),
        ('critical', '极高风险'),
    )
    
    STATUS_CHOICES = (
        ('pending', '待执行'),
        ('approved', '已批准'),
        ('executing', '执行中'),
        ('success', '执行成功'),
        ('failed', '执行失败'),
        ('rejected', '已拒绝'),
        ('cancelled', '已取消'),
    )
    
    # 关联信息
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, verbose_name="数据库")
    related_log = models.ForeignKey(MonitorLog, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="关联监控日志")
    
    # 新增: 触发告警（用于告警与工单关联）
    triggered_by_alert = models.ForeignKey('AlertLog', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="触发告警")
    
    # 新增: 执行上下文
    execution_context = models.JSONField(null=True, blank=True, verbose_name="执行上下文")
    
    # 新增: 执行证据
    execution_evidence = models.JSONField(null=True, blank=True, verbose_name="执行证据")
    
    # 操作信息
    action_type = models.CharField(max_length=50, choices=ACTION_CHOICES, verbose_name="操作类型")
    description = models.TextField(verbose_name="操作描述")
    sql_command = models.TextField(verbose_name="SQL 命令")
    
    # 风险信息
    risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES, default='medium', verbose_name="风险等级")
    rollback_command = models.TextField(blank=True, null=True, verbose_name="回滚命令")
    
    # 审批信息
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="状态")
    approver = models.CharField(max_length=100, blank=True, null=True, verbose_name="审批人")
    approve_time = models.DateTimeField(blank=True, null=True, verbose_name="审批时间")
    
    # 执行信息
    executor = models.CharField(max_length=100, blank=True, null=True, verbose_name="执行人")
    execute_time = models.DateTimeField(blank=True, null=True, verbose_name="执行时间")
    execution_result = models.TextField(blank=True, null=True, verbose_name="执行结果")
    
    # 元数据
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    def __str__(self):
        return f"{self.config.name} - {self.get_action_type_display()} - {self.status}"
    
    class Meta:
        verbose_name = "运维操作审计"
        verbose_name_plural = "运维操作审计列表"
        ordering = ['-create_time']


# ==========================================
# 告警日志（用于去重与状态追踪，v0.1.0 新增）
# ==========================================
class AlertLog(models.Model):
    """
    每条活跃告警对应一条记录。
    同一数据库 + 同一告警类型 + 同一 metric_key 同时只保留一条 active 记录，
    以此实现：首次出现时发送通知，恢复时发送恢复通知，中间不重复推送。
    """
    ALERT_TYPE_CHOICES = (
        ('down',       '实例 DOWN/UP'),
        ('tablespace', '表空间容量'),
        ('connection', '连接数使用率'),
        ('lock',       '锁等待'),
        ('baseline',   '基线偏离'),
    )
    STATUS_CHOICES = (
        ('active',   '活跃'),
        ('resolved', '已恢复'),
    )

    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, verbose_name="数据库")
    alert_type = models.CharField(max_length=50, choices=ALERT_TYPE_CHOICES, verbose_name="告警类型")
    # 新增: 关联工单（用于告警与工单关联）
    related_ticket = models.ForeignKey('AuditLog', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="关联工单")
    # 对于基线告警，存储指标名（如 active_connections）；其他告警类型可留空
    metric_key = models.CharField(max_length=100, blank=True, default='', verbose_name="指标键")
    severity = models.CharField(max_length=20, default='warning', verbose_name="严重程度")
    title = models.CharField(max_length=200, verbose_name="告警标题")
    description = models.TextField(verbose_name="告警详情")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', verbose_name="状态")
    # 最后一次推送通知的时间（用于判断是否需要再次提醒）
    last_notified_at = models.DateTimeField(default=timezone.now, verbose_name="最后通知时间")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="恢复时间")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="首次告警时间")

    class Meta:
        verbose_name = "告警日志"
        verbose_name_plural = "告警日志列表"
        ordering = ['-create_time']
        # 一个数据库 + 告警类型 + 指标键只能有一条 active 记录
        indexes = [
            models.Index(fields=['config', 'alert_type', 'metric_key', 'status']),
        ]

    def __str__(self):
        return f"{self.config.name} | {self.alert_type} | {self.status}"