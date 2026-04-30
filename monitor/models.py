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


# ==========================================
# 用户配置（用于 RBAC 权限控制）
# ==========================================
class UserProfile(models.Model):
    """用户配置信息，用于角色和权限管理"""
    
    ROLE_CHOICES = (
        ('admin', '管理员'),
        ('supervisor', 'Supervisor'),
        ('user', '普通用户'),
        ('readonly', '只读用户'),
    )
    
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='profile', verbose_name="用户")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user', verbose_name="角色")
    allowed_databases = models.JSONField(null=True, blank=True, verbose_name="可访问数据库列表", help_text="为空表示可访问所有数据库，列表形式指定可访问的数据库ID")
    
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"
    
    class Meta:
        verbose_name = "用户配置"
        verbose_name_plural = "用户配置列表"


# ==========================================
# 业务系统（用于业务影响评估）
# ==========================================
class BusinessSystem(models.Model):
    """业务系统配置，用于数据库与业务系统的关联"""
    
    IMPORTANCE_CHOICES = (
        ('critical', '核心'),
        ('important', '重要'),
        ('normal', '一般'),
    )
    
    name = models.CharField(max_length=100, verbose_name="业务系统名称")
    importance = models.CharField(max_length=20, choices=IMPORTANCE_CHOICES, default='normal', verbose_name="重要程度")
    owner = models.CharField(max_length=100, blank=True, null=True, verbose_name="负责人")
    contact = models.CharField(max_length=200, blank=True, null=True, verbose_name="联系方式")
    description = models.TextField(blank=True, null=True, verbose_name="描述")
    
    # 关联的数据库（多对多）
    databases = models.ManyToManyField(DatabaseConfig, blank=True, related_name='business_systems', verbose_name="关联数据库")
    
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    def __str__(self):
        return f"{self.name} ({self.get_importance_display()})"
    
    class Meta:
        verbose_name = "业务系统"
        verbose_name_plural = "业务系统列表"


# ==========================================
# 指标定义（元数据）
# ==========================================
class MetricDefinition(models.Model):
    """指标元数据定义，用于统一管理所有监控指标"""
    
    DIRECTION_CHOICES = (
        ('up', '上升敏感'),
        ('down', '下降敏感'),
        ('both', '双向敏感'),
    )
    
    metric_key = models.CharField(max_length=100, primary_key=True, verbose_name="指标键")
    display_name = models.CharField(max_length=100, verbose_name="显示名称")
    unit = models.CharField(max_length=20, blank=True, null=True, verbose_name="单位", help_text="count/pct/mb/qps/sec")
    db_types = models.JSONField(null=True, blank=True, verbose_name="适用数据库类型", help_text="如 ['oracle','mysql']，为空表示全部")
    alert_direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='up', verbose_name="告警方向")
    sigma_k = models.FloatField(default=2.0, verbose_name="Sigma倍数", help_text="正常范围 = mean ± k*std")
    fixed_warn_val = models.FloatField(null=True, blank=True, verbose_name="固定阈值兜底", help_text="基线未就绪时使用")
    is_capacity = models.BooleanField(default=False, verbose_name="是否容量指标", help_text="是否参与容量预测")
    description = models.TextField(blank=True, null=True, verbose_name="描述")
    
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    def __str__(self):
        return f"{self.display_name} ({self.metric_key})"
    
    class Meta:
        verbose_name = "指标定义"
        verbose_name_plural = "指标定义列表"


# ==========================================
# 动态基线模型（持久化存储）
# ==========================================
class BaselineModel(models.Model):
    """动态基线模型，存储每个数据库×指标×时间槽的基线统计量"""
    
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, verbose_name="数据库")
    metric_key = models.CharField(max_length=100, verbose_name="指标键")
    time_slot = models.IntegerField(verbose_name="时间槽", help_text="0-167（星期几×24 + 小时）")
    sample_count = models.IntegerField(default=0, verbose_name="样本数")
    mean = models.FloatField(default=0.0, verbose_name="均值")
    std = models.FloatField(default=0.0, verbose_name="标准差")
    p90 = models.FloatField(default=0.0, verbose_name="P90")
    p95 = models.FloatField(default=0.0, verbose_name="P95")
    p99 = models.FloatField(default=0.0, verbose_name="P99")
    normal_min = models.FloatField(default=0.0, verbose_name="正常下限")
    normal_max = models.FloatField(default=0.0, verbose_name="正常上限")
    data_sufficient = models.BooleanField(default=False, verbose_name="数据充分")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        verbose_name = "基线模型"
        verbose_name_plural = "基线模型列表"
        unique_together = ('config', 'metric_key', 'time_slot')
        indexes = [
            models.Index(fields=['config', 'metric_key', 'time_slot']),
        ]
    
    def __str__(self):
        return f"{self.config.name} | {self.metric_key} | slot={self.time_slot}"


# ==========================================
# 容量预测结果
# ==========================================
class PredictionResult(models.Model):
    """容量预测结果，存储每个数据库的容量预测数据"""
    
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, verbose_name="数据库")
    metric_key = models.CharField(max_length=100, verbose_name="指标键")
    resource_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="资源名称", help_text="如表空间名")
    current_value = models.FloatField(null=True, blank=True, verbose_name="当前值")
    monthly_growth_rate = models.FloatField(null=True, blank=True, verbose_name="月增长率(%)")
    predicted_warn_date = models.DateField(null=True, blank=True, verbose_name="预计触达告警线日期")
    predicted_crit_date = models.DateField(null=True, blank=True, verbose_name="预计触达危险线日期")
    model_used = models.CharField(max_length=50, blank=True, null=True, verbose_name="使用的模型")
    confidence = models.FloatField(null=True, blank=True, verbose_name="置信度")
    recommendation = models.TextField(blank=True, null=True, verbose_name="建议")
    generated_at = models.DateTimeField(auto_now=True, verbose_name="生成时间")
    
    class Meta:
        verbose_name = "容量预测结果"
        verbose_name_plural = "容量预测结果列表"
        unique_together = ('config', 'metric_key', 'resource_name')
    
    def __str__(self):
        return f"{self.config.name} | {self.metric_key} | {self.model_used}"


# ==========================================
# 健康评分记录
# ==========================================
class HealthScore(models.Model):
    """健康评分记录，每日为每个数据库生成"""
    
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, verbose_name="数据库")
    score_date = models.DateField(verbose_name="评分日期")
    total_score = models.FloatField(verbose_name="总分", help_text="0-100")
    availability_score = models.FloatField(default=0.0, verbose_name="可用性得分")
    capacity_score = models.FloatField(default=0.0, verbose_name="容量得分")
    performance_score = models.FloatField(default=0.0, verbose_name="性能得分")
    config_score = models.FloatField(default=0.0, verbose_name="配置得分")
    ops_score = models.FloatField(default=0.0, verbose_name="运维得分")
    grade = models.CharField(max_length=5, blank=True, null=True, verbose_name="等级")
    score_detail = models.JSONField(null=True, blank=True, verbose_name="评分详情")
    
    class Meta:
        verbose_name = "健康评分"
        verbose_name_plural = "健康评分列表"
        unique_together = ('config', 'score_date')
        ordering = ['-score_date']
    
    def __str__(self):
        return f"{self.config.name} | {self.score_date} | {self.total_score}"


# ==========================================
# 告警静默窗口
# ==========================================
class AlertSilenceWindow(models.Model):
    """告警静默窗口配置，用于维护期间静默告警"""
    
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, null=True, blank=True, verbose_name="数据库", help_text="为空表示全局静默")
    name = models.CharField(max_length=100, verbose_name="静默窗口名称")
    alert_type = models.CharField(max_length=50, blank=True, default='', verbose_name="告警类型", help_text="为空表示所有类型")
    
    # 时间配置
    start_time = models.TimeField(verbose_name="开始时间")
    end_time = models.TimeField(verbose_name="结束时间")
    weekdays = models.CharField(max_length=20, default='1,2,3,4,5,6,7', verbose_name="星期几", help_text="逗号分隔，1=周一,7=周日")
    
    # 一次性静默
    start_datetime = models.DateTimeField(null=True, blank=True, verbose_name="精确开始时间")
    end_datetime = models.DateTimeField(null=True, blank=True, verbose_name="精确结束时间")
    
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    reason = models.TextField(blank=True, null=True, verbose_name="静默原因")
    created_by = models.CharField(max_length=100, blank=True, null=True, verbose_name="创建人")
    
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    def is_in_window(self):
        """检查当前时间是否在静默窗口内"""
        now = timezone.now()
        
        # 检查一次性静默
        if self.start_datetime and self.end_datetime:
            return self.start_datetime <= now <= self.end_datetime
        
        # 检查周期性静默
        current_weekday = now.isoweekday()  # 1=周一, 7=周日
        weekdays_list = [int(d.strip()) for d in self.weekdays.split(',') if d.strip()]
        
        if current_weekday not in weekdays_list:
            return False
        
        current_time = now.time()
        if self.start_time <= self.end_time:
            return self.start_time <= current_time <= self.end_time
        else:
            # 跨午夜
            return current_time >= self.start_time or current_time <= self.end_time
    
    def __str__(self):
        return f"{self.name} ({self.start_time}-{self.end_time})"
    
    class Meta:
        verbose_name = "告警静默窗口"
        verbose_name_plural = "告警静默窗口列表"


# ==========================================
# 告警通知日志
# ==========================================
class AlertNotificationLog(models.Model):
    """告警通知发送日志，记录每次通知的发送结果"""
    
    CHANNEL_CHOICES = (
        ('email', '邮件'),
        ('dingtalk', '钉钉'),
        ('wecom', '企业微信'),
        ('sms', '短信'),
    )
    STATUS_CHOICES = (
        ('success', '发送成功'),
        ('failed', '发送失败'),
        ('skipped', '跳过'),
    )
    
    alert = models.ForeignKey(AlertLog, on_delete=models.CASCADE, related_name='notifications', verbose_name="告警")
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, verbose_name="通知渠道")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name="发送状态")
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    send_time = models.DateTimeField(auto_now_add=True, verbose_name="发送时间")
    
    class Meta:
        verbose_name = "告警通知日志"
        verbose_name_plural = "告警通知日志列表"
        ordering = ['-send_time']
    
    def __str__(self):
        return f"{self.alert.title} | {self.channel} | {self.status}"


# ==========================================
# 审批流程（多级审批）
# ==========================================
class ApprovalStep(models.Model):
    """审批步骤定义，定义每个风险等级需要的审批流程"""
    
    RISK_LEVEL_CHOICES = (
        ('low', '低风险'),
        ('medium', '中风险'),
        ('high', '高风险'),
        ('critical', '极高风险'),
    )
    
    risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES, verbose_name="风险等级")
    step_order = models.IntegerField(verbose_name="步骤顺序", help_text="从1开始")
    approver_role = models.CharField(max_length=50, verbose_name="审批角色", help_text="如 admin, supervisor")
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name="步骤描述")
    is_required = models.BooleanField(default=True, verbose_name="是否必须")
    
    class Meta:
        verbose_name = "审批步骤"
        verbose_name_plural = "审批步骤列表"
        unique_together = ('risk_level', 'step_order')
        ordering = ['risk_level', 'step_order']
    
    def __str__(self):
        return f"{self.get_risk_level_display()} - 步骤{self.step_order}: {self.approver_role}"


class ApprovalRecord(models.Model):
    """审批记录，记录每个工单的每一步审批"""
    
    ACTION_CHOICES = (
        ('approve', '批准'),
        ('reject', '拒绝'),
        ('comment', '评论'),
    )
    
    audit_log = models.ForeignKey(AuditLog, on_delete=models.CASCADE, related_name='approval_records', verbose_name="工单")
    step_order = models.IntegerField(verbose_name="步骤顺序")
    approver = models.CharField(max_length=100, verbose_name="审批人")
    approver_role = models.CharField(max_length=50, verbose_name="审批角色")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name="审批动作")
    comment = models.TextField(blank=True, null=True, verbose_name="审批意见")
    action_time = models.DateTimeField(auto_now_add=True, verbose_name="审批时间")
    
    class Meta:
        verbose_name = "审批记录"
        verbose_name_plural = "审批记录列表"
        ordering = ['audit_log', 'step_order']
    
    def __str__(self):
        return f"{self.audit_log} | 步骤{self.step_order} | {self.approver} | {self.action}"


# ==========================================
# 平台指标（可观测性）
# ==========================================
class PlatformMetric(models.Model):
    """平台自身运行指标，用于平台自监控"""
    
    METRIC_TYPE_CHOICES = (
        ('counter', '计数器'),
        ('gauge', '仪表盘'),
        ('histogram', '直方图'),
    )
    
    name = models.CharField(max_length=100, verbose_name="指标名称")
    metric_type = models.CharField(max_length=20, choices=METRIC_TYPE_CHOICES, verbose_name="指标类型")
    value = models.FloatField(verbose_name="当前值")
    labels = models.JSONField(null=True, blank=True, verbose_name="标签")
    help_text = models.CharField(max_length=200, blank=True, null=True, verbose_name="说明")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        verbose_name = "平台指标"
        verbose_name_plural = "平台指标列表"
        unique_together = ('name', 'labels')
    
    def __str__(self):
        return f"{self.name} = {self.value}"