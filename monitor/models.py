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


# =============================================================================
# 4NF 子表读写辅助函数
# =============================================================================
def _replace_scalar_children(parent, child_model, values, fk_field='rule'):
    """整表替换标量多值子行（先删后批量插）。values 为标量列表。"""
    if parent.pk is None:
        parent.save()
    child_model.objects.filter(**{fk_field: parent}).delete()
    objs = [child_model(**{fk_field: parent, 'value': str(v)}) for v in (values or []) if v is not None and v != '']
    child_model.objects.bulk_create(objs)


def _replace_ordered_children(parent, child_model, payloads, fk_field, phase=None):
    """整表替换有序/复合多值子行。payloads 为 dict/list 元素列表，seq 保序。"""
    if parent.pk is None:
        parent.save()
    qs = child_model.objects.filter(**{fk_field: parent})
    if phase is not None:
        qs = qs.filter(phase=phase)
        objs = [child_model(**{fk_field: parent, 'phase': phase, 'seq': i, 'payload': p})
                for i, p in enumerate(payloads or [])]
    else:
        objs = [child_model(**{fk_field: parent, 'seq': i, 'payload': p})
                for i, p in enumerate(payloads or [])]
    qs.delete()
    child_model.objects.bulk_create(objs)


def _read_ordered_children(parent, manager_name, phase=None):
    """按 seq 读出 payload 列表。"""
    qs = getattr(parent, manager_name)
    if phase is not None:
        qs = qs.filter(phase=phase)
    return [o.payload for o in qs.order_by('seq')]

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

    # Phase 7A-08: CPU 核数 (性能主页 Max CPU 线; 采集可得时以采集值优先, 此为手工兜底)
    cpu_cores = models.IntegerField(null=True, blank=True, verbose_name="CPU核数")

    # 密码轮换相关字段（Phase 4 新增）
    password_changed_at = models.DateTimeField(null=True, blank=True, verbose_name="密码最后修改时间")
    password_expiry_days = models.IntegerField(default=90, verbose_name="密码过期天数", help_text="默认90天，0表示不过期")
    # Phase 8E: 自治等级 (null=跟随全局 AUTONOMY_DEFAULT_LEVEL; 0=观察 1=半自动 2=低风险自动 3=扩展自动)
    autonomy_level = models.IntegerField(null=True, blank=True, verbose_name="自治等级",
        help_text="留空表示跟随全局默认; 0-3 逐级放权")

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
        ('API_CREATE', 'API 创建'),
        ('API_UPDATE', 'API 更新'),
        ('API_DELETE', 'API 删除'),
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
    # BUG-136: config 原为 NOT NULL，但 AuditLogMiddleware 对所有写请求建审计记录，
    # 而登录、用户管理、角色配置这类平台级操作根本没有关联实例 —— INSERT 触发
    # NotNullViolation，被中间件的 except 吞掉，审计记录静默丢失。
    # 审计追踪是合规特性，"大部分写操作查不到记录"是实质性缺陷。
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
                               null=True, blank=True, verbose_name="数据库",
                               help_text="平台级操作（登录/用户/角色）无关联实例，可为空")
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
        ('active',       '活跃'),
        ('acknowledged', '已确认'),
        ('resolved',     '已恢复'),
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
# 角色（RBAC 权限控制）
# ==========================================
class Role(models.Model):
    """角色定义，支持内置角色和自定义角色"""

    code = models.CharField(max_length=50, unique=True, verbose_name="角色编码", help_text="如 super_admin, dba, auditor")
    name = models.CharField(max_length=100, verbose_name="角色名称", help_text="如 超级管理员, 数据库管理员")
    description = models.TextField(blank=True, default='', verbose_name="角色描述")
    is_builtin = models.BooleanField(default=False, verbose_name="是否内置角色", help_text="内置角色不可删除")

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        prefix = "[内置] " if self.is_builtin else ""
        return f"{prefix}{self.name} ({self.code})"

    class Meta:
        verbose_name = "角色"
        verbose_name_plural = "角色列表"
        ordering = ['is_builtin', 'code']


class RolePermission(models.Model):
    """角色权限关联，每条记录代表一个角色拥有一个权限编码"""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='permissions', verbose_name="角色")
    permission_code = models.CharField(max_length=100, verbose_name="权限编码", help_text="格式: module.action，如 databases.view")

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "角色权限"
        verbose_name_plural = "角色权限列表"
        unique_together = [('role', 'permission_code')]
        indexes = [
            models.Index(fields=['permission_code']),
        ]

    def __str__(self):
        return f"{self.role.name} -> {self.permission_code}"


# ==========================================
# 用户配置（用于 RBAC 权限控制）
# ==========================================
class UserProfile(models.Model):
    """用户配置信息，用于角色和数据范围管理"""

    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='profile', verbose_name="用户")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name='users', verbose_name="角色")
    # 4NF: allowed_databases 多值已拆为 UserProfileDatabase 子表

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def allowed_databases(self):
        return [o.config_id for o in self.alloweddatabase_set.order_by('id')]
    @allowed_databases.setter
    def allowed_databases(self, values):
        if self.pk is None:
            self.save()
        UserProfileDatabase.objects.filter(profile=self).delete()
        UserProfileDatabase.objects.bulk_create(
            UserProfileDatabase(profile=self, config_id=int(v)) for v in (values or []) if v is not None)

    def __str__(self):
        role_name = self.role.name if self.role else '无角色'
        return f"{self.user.username} - {role_name}"

    class Meta:
        verbose_name = "用户配置"
        verbose_name_plural = "用户配置列表"


class UserProfileDatabase(models.Model):
    """4NF 子表：用户可访问数据库（多值拆分）

    BUG-130: config_id 原为裸 IntegerField，删除 DatabaseConfig 后授权记录残留。
    若新实例复用了同一自增 ID（手工 setval / 数据迁移导入），旧授权会意外套用
    到新实例上 —— 静默的越权。改为真外键并级联删除；db_column 保持 config_id
    不变，因此 `o.config_id` 的既有读法与列名都不受影响。
    """
    profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='alloweddatabase_set')
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
                               db_column='config_id', related_name='+',
                               verbose_name="可访问数据库")

    class Meta:
        unique_together = [('profile', 'config')]


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
    # 4NF: db_types 多值已拆为 MetricDefinitionDbType 子表
    alert_direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default="up", verbose_name="告警方向")
    sigma_k = models.FloatField(default=2.0, verbose_name="Sigma倍数", help_text="正常范围 = mean ± k*std")
    fixed_warn_val = models.FloatField(null=True, blank=True, verbose_name="固定阈值兜底", help_text="基线未就绪时使用")
    is_capacity = models.BooleanField(default=False, verbose_name="是否容量指标", help_text="是否参与容量预测")
    description = models.TextField(blank=True, null=True, verbose_name="描述")
    
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    @property
    def db_types(self):
        return [o.value for o in self.dbtype_set.order_by('id')]
    @db_types.setter
    def db_types(self, values):
        _replace_scalar_children(self, MetricDefinitionDbType, values, fk_field='metric')

    def __str__(self):
        return f"{self.display_name} ({self.metric_key})"

    class Meta:
        verbose_name = "指标定义"
        verbose_name_plural = "指标定义列表"


class MetricDefinitionDbType(models.Model):
    """4NF 子表：指标定义-适用数据库类型（多值拆分）"""
    metric = models.ForeignKey(MetricDefinition, on_delete=models.CASCADE, related_name='dbtype_set')
    value = models.CharField(max_length=20)
    class Meta:
        unique_together = [('metric', 'value')]


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
# 告警模板组（多模板支持）
# ==========================================
class AlertTemplate(models.Model):
    """告警模板组（多模板支持）- 按数据库类型创建多个命名模板，如"生产库-严格"、"测试库-宽松"等"""

    name = models.CharField(max_length=100, verbose_name="模板名称", help_text="例如：生产库-严格、测试库-宽松")
    db_type = models.CharField(max_length=20, choices=DB_TYPES, verbose_name="数据库类型")
    is_default = models.BooleanField(default=False, verbose_name="是否为默认模板")
    description = models.TextField(blank=True, null=True, verbose_name="模板描述")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        default_mark = ' [默认]' if self.is_default else ''
        return f"[{self.db_type}] {self.name}{default_mark}"

    class Meta:
        verbose_name = "告警模板组"
        verbose_name_plural = "告警模板组列表"
        unique_together = ('name', 'db_type')
        ordering = ['db_type', 'name']


class AlertThresholdTemplate(models.Model):
    """告警阈值规则，属于某个告警模板组，定义单个指标的多级告警规则"""

    RULE_TYPE_CHOICES = (
        ('threshold', '固定阈值'),
        ('baseline_amplitude', '基线振幅'),
    )
    DIRECTION_CHOICES = (
        ('up', '上升触发'),
        ('down', '下降触发'),
        ('both', '双向触发'),
    )

    template = models.ForeignKey(
        AlertTemplate, on_delete=models.CASCADE, null=True, blank=True,
        related_name='rules', verbose_name="所属模板组"
    )
    db_type = models.CharField(max_length=20, choices=DB_TYPES, verbose_name="数据库类型")
    metric_key = models.CharField(max_length=100, verbose_name="指标键")
    display_name = models.CharField(max_length=100, verbose_name="指标显示名")
    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES, default='threshold', verbose_name="规则类型")
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='up', verbose_name="触发方向")

    # 固定阈值模式的三级阈值
    warn_threshold = models.FloatField(null=True, blank=True, verbose_name="一级告警阈值(warning)")
    error_threshold = models.FloatField(null=True, blank=True, verbose_name="二级告警阈值(error)")
    critical_threshold = models.FloatField(null=True, blank=True, verbose_name="三级告警阈值(critical)")

    # 基线振幅模式的三级百分比偏差阈值
    warn_amplitude_pct = models.FloatField(null=True, blank=True, verbose_name="一级振幅阈值(%) warning")
    error_amplitude_pct = models.FloatField(null=True, blank=True, verbose_name="二级振幅阈值(%) error")
    critical_amplitude_pct = models.FloatField(null=True, blank=True, verbose_name="三级振幅阈值(%) critical")

    unit = models.CharField(max_length=20, blank=True, null=True, verbose_name="单位")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    description = models.TextField(blank=True, null=True, verbose_name="描述")

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"[{self.db_type}] {self.display_name} ({self.rule_type})"

    class Meta:
        verbose_name = "告警阈值规则"
        verbose_name_plural = "告警阈值规则列表"
        unique_together = ('template', 'metric_key')
        ordering = ['template', 'metric_key']


# ==========================================
# 数据库告警覆盖配置（个性化）
# ==========================================
class DatabaseAlertOverride(models.Model):
    """针对特定数据库的告警配置覆盖，优先级高于模板规则"""

    RULE_TYPE_CHOICES = AlertThresholdTemplate.RULE_TYPE_CHOICES
    DIRECTION_CHOICES = AlertThresholdTemplate.DIRECTION_CHOICES

    db_config = models.ForeignKey(
        DatabaseConfig, on_delete=models.CASCADE,
        related_name='alert_overrides', verbose_name="数据库"
    )
    metric_key = models.CharField(max_length=100, verbose_name="指标键")

    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES, null=True, blank=True, verbose_name="规则类型覆盖")
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, null=True, blank=True, verbose_name="触发方向覆盖")

    warn_threshold = models.FloatField(null=True, blank=True, verbose_name="一级告警阈值覆盖")
    error_threshold = models.FloatField(null=True, blank=True, verbose_name="二级告警阈值覆盖")
    critical_threshold = models.FloatField(null=True, blank=True, verbose_name="三级告警阈值覆盖")

    warn_amplitude_pct = models.FloatField(null=True, blank=True, verbose_name="一级振幅阈值覆盖(%)")
    error_amplitude_pct = models.FloatField(null=True, blank=True, verbose_name="二级振幅阈值覆盖(%)")
    critical_amplitude_pct = models.FloatField(null=True, blank=True, verbose_name="三级振幅阈值覆盖(%)")

    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    note = models.TextField(blank=True, null=True, verbose_name="备注")

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.db_config.name} | {self.metric_key}"

    class Meta:
        verbose_name = "数据库告警配置覆盖"
        verbose_name_plural = "数据库告警配置覆盖列表"
        unique_together = ('db_config', 'metric_key')
        ordering = ['db_config', 'metric_key']


# ==========================================
# 数据库模板分配
# ==========================================
class DatabaseTemplateAssignment(models.Model):
    """数据库与告警模板组的关联关系"""

    db_config = models.OneToOneField(
        DatabaseConfig, on_delete=models.CASCADE,
        related_name='template_assignment', verbose_name="数据库"
    )
    template = models.ForeignKey(
        AlertTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_databases', verbose_name="使用的模板组"
    )
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name="分配时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    note = models.TextField(blank=True, null=True, verbose_name="备注")

    def __str__(self):
        tpl_name = self.template.name if self.template else '未分配'
        return f"{self.db_config.name} → {tpl_name}"

    class Meta:
        verbose_name = "数据库模板分配"
        verbose_name_plural = "数据库模板分配列表"


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


# ==========================================
# 通知规则（Phase 4 新增）
# ==========================================
class NotificationRule(models.Model):
    """通知规则配置，定义告警路由策略"""

    name = models.CharField(max_length=100, verbose_name="规则名称")
    # 4NF: alert_types/severities/channels 三个独立多值集合已拆分为子表
    # (NotificationRuleAlertType/Severity/Channel)，此处以兼容属性暴露
    db_config = models.ForeignKey(
        DatabaseConfig, on_delete=models.CASCADE,
        null=True, blank=True, verbose_name="数据库",
        help_text="为空表示全局规则"
    )
    schedule = models.JSONField(null=True, blank=True, verbose_name="时间策略",
        help_text="{'work_hours': true, 'start': '09:00', 'end': '18:00', 'weekdays': '1,2,3,4,5'}")
    escalation_minutes = models.IntegerField(default=0, verbose_name="升级等待时间(分钟)",
        help_text="0 表示不升级，>0 表示未确认 N 分钟后自动提升等级")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    priority = models.IntegerField(default=0, verbose_name="优先级",
        help_text="数字越大优先级越高，同告警匹配多规则时取最高优先级")

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        scope = self.db_config.name if self.db_config else '全局'
        return f"[{scope}] {self.name}"

    # ---- 4NF 兼容属性（读/写子表）----
    @property
    def alert_types(self):
        return [o.value for o in self.alerttype_set.order_by('id')]
    @alert_types.setter
    def alert_types(self, values):
        _replace_scalar_children(self, NotificationRuleAlertType, values)
    @property
    def severities(self):
        return [o.value for o in self.severity_set.order_by('id')]
    @severities.setter
    def severities(self, values):
        _replace_scalar_children(self, NotificationRuleSeverity, values)
    @property
    def channels(self):
        return [o.value for o in self.channel_set.order_by('id')]
    @channels.setter
    def channels(self, values):
        _replace_scalar_children(self, NotificationRuleChannel, values)

    class Meta:
        verbose_name = "通知规则"
        verbose_name_plural = "通知规则列表"
        ordering = ['-priority', 'name']


class NotificationRuleAlertType(models.Model):
    """4NF 子表：通知规则-告警类型（多值拆分）"""
    rule = models.ForeignKey(NotificationRule, on_delete=models.CASCADE, related_name='alerttype_set')
    value = models.CharField(max_length=50)
    class Meta:
        unique_together = [('rule', 'value')]


class NotificationRuleSeverity(models.Model):
    """4NF 子表：通知规则-严重程度（多值拆分）"""
    rule = models.ForeignKey(NotificationRule, on_delete=models.CASCADE, related_name='severity_set')
    value = models.CharField(max_length=20)
    class Meta:
        unique_together = [('rule', 'value')]


class NotificationRuleChannel(models.Model):
    """4NF 子表：通知规则-通知渠道（多值拆分）"""
    rule = models.ForeignKey(NotificationRule, on_delete=models.CASCADE, related_name='channel_set')
    value = models.CharField(max_length=20)
    class Meta:
        unique_together = [('rule', 'value')]


# ==========================================
# 数据库拓扑（Phase 4 新增）
# ==========================================
class DatabaseTopology(models.Model):
    """数据库拓扑关系，描述主从/RAC/ADG等架构关系"""

    ROLE_CHOICES = (
        ('primary', '主库'), ('standby', '备库'),
        ('rac_node', 'RAC节点'), ('dsc_node', 'DSC节点'),
        ('single', '单机'),
    )
    TOPOLOGY_TYPE_CHOICES = (
        ('primary_standby', '主从'), ('rac', 'RAC'),
        ('adg', 'Active Data Guard'), ('mha', 'MHA'),
        ('dsc', 'DSC集群'), ('dts', 'DTS复制'),
        ('single', '单机'),
    )
    SYNC_MODE_CHOICES = (
        ('sync', '同步'), ('async', '异步'),
        ('semi_sync', '半同步'), ('', '未知'),
    )

    db_config = models.ForeignKey(
        DatabaseConfig, on_delete=models.CASCADE,
        related_name='topology_info', verbose_name="数据库"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='single', verbose_name="角色")
    topology_type = models.CharField(max_length=20, choices=TOPOLOGY_TYPE_CHOICES, default='single', verbose_name="拓扑类型")
    cluster_name = models.CharField(max_length=100, blank=True, default='', verbose_name="集群名称")
    peer_databases = models.ManyToManyField(DatabaseConfig, blank=True, related_name='peer_topologies', verbose_name="关联数据库")
    sync_mode = models.CharField(max_length=20, choices=SYNC_MODE_CHOICES, blank=True, default='', verbose_name="同步模式")
    lag_seconds = models.FloatField(null=True, blank=True, verbose_name="延迟秒数")

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.db_config.name} [{self.get_role_display()}] {self.get_topology_type_display()}"

    class Meta:
        verbose_name = "数据库拓扑"
        verbose_name_plural = "数据库拓扑列表"


# ==========================================
# 报表记录（Phase 4 新增）
# ==========================================
class ReportRecord(models.Model):
    """报表生成记录"""

    REPORT_TYPE_CHOICES = (
        ('daily', '日报'), ('weekly', '周报'), ('monthly', '月报'),
    )
    STATUS_CHOICES = (
        ('generated', '已生成'), ('sent', '已发送'), ('failed', '发送失败'),
    )

    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES, verbose_name="报表类型")
    title = models.CharField(max_length=200, verbose_name="报表标题")
    content_html = models.TextField(blank=True, default='', verbose_name="HTML内容")
    file_path = models.CharField(max_length=500, blank=True, default='', verbose_name="文件路径")
    period_start = models.DateField(verbose_name="统计周期开始")
    period_end = models.DateField(verbose_name="统计周期结束")
    # 4NF: recipients 多值已拆为 ReportRecordRecipient 子表
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='generated', verbose_name="状态")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    @property
    def recipients(self):
        return [o.value for o in self.recipient_set.order_by('id')]
    @recipients.setter
    def recipients(self, values):
        _replace_scalar_children(self, ReportRecordRecipient, values, fk_field='report')

    def __str__(self):
        return f"{self.title} ({self.get_report_type_display()})"

    class Meta:
        verbose_name = "报表记录"
        verbose_name_plural = "报表记录列表"
        ordering = ['-created_at']


class ReportRecordRecipient(models.Model):
    """4NF 子表：报表记录-收件人（多值拆分）"""
    report = models.ForeignKey(ReportRecord, on_delete=models.CASCADE, related_name='recipient_set')
    value = models.CharField(max_length=200)
    class Meta:
        unique_together = [('report', 'value')]


# ==========================================
# Phase 5: 告警案例库（RCA 2.0）
# ==========================================
class AlertCase(models.Model):
    """历史告警处置案例库 - 用于相似度匹配和方案复用"""
    case_id = models.CharField(max_length=64, unique=True, verbose_name="案例ID")
    title = models.CharField(max_length=200, verbose_name="案例标题")
    db_type = models.CharField(max_length=20, choices=DB_TYPES, verbose_name="数据库类型")
    symptom_signature = models.JSONField(default=dict, verbose_name="症状特征向量",
        help_text="触发该案例的关键指标快照，如 {conn_usage_pct: 95, ...}")
    root_cause = models.TextField(verbose_name="根因描述")
    resolution = models.TextField(verbose_name="解决方案")
    sql_used = models.TextField(blank=True, default='', verbose_name="使用的SQL")
    # 4NF: commands_used/tags/references 三个独立多值集合已拆为子表
    severity = models.CharField(max_length=20, default='warning', verbose_name="严重程度")
    success_count = models.IntegerField(default=0, verbose_name="成功引用次数")
    fail_count = models.IntegerField(default=0, verbose_name="失败引用次数")
    confidence = models.FloatField(default=0.0, verbose_name="案例置信度",
        help_text="基于成功/失败比计算")
    # Phase 8B: 案例来源与向量索引状态
    source = models.CharField(max_length=12, default='manual', db_index=True, verbose_name="案例来源",
        help_text="manual=人工录入 distilled=事故自动蒸馏 seed=初始化种子")
    source_incident = models.CharField(max_length=48, default='', blank=True, db_index=True,
        verbose_name="来源事故ID")
    embedding_indexed = models.BooleanField(default=False, verbose_name="向量已索引",
        help_text="True=已写入 ES db_cases 向量索引")
    created_by = models.CharField(max_length=100, blank=True, default='', verbose_name="创建人")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name="最后引用时间")

    @property
    def commands_used(self):
        return [o.value for o in self.command_set.order_by('id')]
    @commands_used.setter
    def commands_used(self, values):
        _replace_scalar_children(self, AlertCaseCommand, values, fk_field='case')
    @property
    def tags(self):
        return [o.value for o in self.tag_set.order_by('id')]
    @tags.setter
    def tags(self, values):
        _replace_scalar_children(self, AlertCaseTag, values, fk_field='case')
    @property
    def references(self):
        return [o.value for o in self.reference_set.order_by('id')]
    @references.setter
    def references(self, values):
        _replace_scalar_children(self, AlertCaseReference, values, fk_field='case')

    def __str__(self):
        return f"{self.case_id}: {self.title}"

    class Meta:
        verbose_name = "告警案例库"
        verbose_name_plural = "告警案例库"
        ordering = ['-update_time']
        indexes = [
            models.Index(fields=['db_type', 'severity']),
        ]


class AlertCaseCommand(models.Model):
    """4NF 子表：案例-使用命令（多值拆分）"""
    case = models.ForeignKey(AlertCase, on_delete=models.CASCADE, related_name='command_set')
    value = models.TextField()
    class Meta:
        unique_together = [('case', 'value')]


class AlertCaseTag(models.Model):
    """4NF 子表：案例-标签（多值拆分）"""
    case = models.ForeignKey(AlertCase, on_delete=models.CASCADE, related_name='tag_set')
    value = models.CharField(max_length=50)
    class Meta:
        unique_together = [('case', 'value')]


class AlertCaseReference(models.Model):
    """4NF 子表：案例-参考链接（多值拆分）"""
    case = models.ForeignKey(AlertCase, on_delete=models.CASCADE, related_name='reference_set')
    value = models.TextField()
    class Meta:
        unique_together = [('case', 'value')]


class RemediationPlan(models.Model):
    """处置方案 - 每次告警生成的修复方案"""
    RISK_CHOICES = (
        ('low', '低风险'),
        ('medium', '中风险'),
        ('high', '高风险'),
        ('critical', '极高风险'),
    )
    SCENARIO_CHOICES = (
        ('conservative', '保守方案'),
        ('standard', '标准方案'),
        ('aggressive', '激进方案'),
    )
    STATUS_CHOICES = (
        ('pending', '待执行'),
        ('approved', '已批准'),
        ('rejected', '已拒绝'),
        ('executing', '执行中'),
        ('success', '执行成功'),
        ('failed', '执行失败'),
        ('cancelled', '已取消'),
    )

    plan_id = models.CharField(max_length=64, unique=True, verbose_name="方案ID")
    alert = models.ForeignKey(AlertLog, on_delete=models.CASCADE,
        related_name='remediation_plans', null=True, blank=True, verbose_name="关联告警")
    db_config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
        related_name='remediation_plans', verbose_name="数据库")
    rule_id = models.CharField(max_length=20, blank=True, default='', verbose_name="RCA规则ID")
    scenario = models.CharField(max_length=20, choices=SCENARIO_CHOICES, verbose_name="方案类型")
    risk_level = models.CharField(max_length=20, choices=RISK_CHOICES, verbose_name="风险等级")
    title = models.CharField(max_length=200, verbose_name="方案标题")
    description = models.TextField(verbose_name="方案描述")
    # 4NF: steps 多值已拆为 RemediationPlanStep 子表
    rollback_plan = models.TextField(blank=True, default='', verbose_name="回滚方案")
    estimated_impact = models.TextField(blank=True, default='', verbose_name="预期影响")
    business_impact_summary = models.JSONField(default=dict, verbose_name="业务影响摘要")
    requires_approval = models.BooleanField(default=False, verbose_name="是否需要审批")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="状态")
    matched_case = models.ForeignKey(AlertCase, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='matched_plans', verbose_name="匹配案例")
    matched_case_similarity = models.FloatField(default=0.0, verbose_name="案例相似度")
    executed_by = models.CharField(max_length=100, blank=True, default='', verbose_name="执行人")
    executed_at = models.DateTimeField(null=True, blank=True, verbose_name="执行时间")
    execution_result = models.TextField(blank=True, default='', verbose_name="执行结果")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def steps(self):
        return _read_ordered_children(self, 'step_items')
    @steps.setter
    def steps(self, values):
        _replace_ordered_children(self, RemediationPlanStep, values, fk_field='plan')

    def __str__(self):
        return f"{self.plan_id} [{self.scenario}|{self.risk_level}] {self.status}"

    class Meta:
        verbose_name = "处置方案"
        verbose_name_plural = "处置方案列表"
        ordering = ['-create_time']


class BusinessImpactAssessment(models.Model):
    """业务影响评估记录"""
    SEVERITY_CHOICES = (
        ('fatal', '致命'),
        ('severe', '严重'),
        ('moderate', '中等'),
        ('minor', '轻微'),
        ('none', '无影响'),
    )

    alert = models.ForeignKey(AlertLog, on_delete=models.CASCADE,
        related_name='business_impacts', null=True, blank=True, verbose_name="关联告警")
    db_config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
        related_name='business_impacts', verbose_name="数据库")
    overall_severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, verbose_name="综合严重度")
    health_score_before = models.FloatField(verbose_name="健康度评估前")
    health_score_after = models.FloatField(verbose_name="健康度评估后")
    # 4NF: health_affected_dimensions/affected_systems 两个独立多值集合已拆为子表
    critical_systems_affected = models.IntegerField(default=0, verbose_name="核心系统受影响数")
    estimated_loss_per_minute = models.FloatField(default=0.0, verbose_name="估算损失(元/分钟)")
    estimated_loss_per_hour = models.FloatField(default=0.0, verbose_name="估算损失(元/小时)")
    sla_breach_risk = models.CharField(max_length=20, default='low',
        verbose_name="SLA违约风险",
        help_text="low/medium/high/critical")
    detail = models.JSONField(default=dict, verbose_name="详细评估数据")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    @property
    def health_affected_dimensions(self):
        return [o.value for o in self.dimension_set.order_by('id')]
    @health_affected_dimensions.setter
    def health_affected_dimensions(self, values):
        _replace_scalar_children(self, BiaDimension, values, fk_field='assessment')
    @property
    def affected_systems(self):
        return _read_ordered_children(self, 'system_set')
    @affected_systems.setter
    def affected_systems(self, values):
        _replace_ordered_children(self, BiaSystem, values, fk_field='assessment')

    class Meta:
        verbose_name = "业务影响评估"
        verbose_name_plural = "业务影响评估列表"
        ordering = ['-create_time']


class BiaDimension(models.Model):
    """4NF 子表：业务影响评估-受影响健康度维度（多值拆分）"""
    assessment = models.ForeignKey(BusinessImpactAssessment, on_delete=models.CASCADE, related_name='dimension_set')
    value = models.CharField(max_length=50)
    class Meta:
        unique_together = [('assessment', 'value')]


class BiaSystem(models.Model):
    """4NF 子表：业务影响评估-受影响业务系统（有序多值拆分）"""
    assessment = models.ForeignKey(BusinessImpactAssessment, on_delete=models.CASCADE, related_name='system_set')
    seq = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    class Meta:
        unique_together = [('assessment', 'seq')]
        ordering = ['seq']


# ==========================================
# Phase 5: 智能巡检引擎
# ==========================================
class InspectionItem(models.Model):
    """巡检项定义 - 所有可执行的检查项"""
    CATEGORY_CHOICES = (
        ('tablespace', '表空间'),
        ('index', '索引'),
        ('object', '对象'),
        ('log', '日志'),
        ('replication', '复制'),
        ('cluster', '集群'),
        ('task', '自动任务'),
        ('performance', '性能'),
        ('security', '安全'),
        ('capacity', '容量'),
        ('config', '配置'),
        ('awr', 'AWR'),
        ('sequence', '序列'),
        ('statistics', '统计信息'),
    )
    LEVEL_CHOICES = (
        ('daily', '日检'),
        ('weekly', '周检'),
        ('monthly', '月检'),
    )
    SEVERITY_CHOICES = (
        ('info', '信息'),
        ('warn', '警告'),
        ('error', '错误'),
        ('critical', '严重'),
    )

    item_id = models.CharField(max_length=64, unique=True, verbose_name="巡检项ID")
    title = models.CharField(max_length=200, verbose_name="巡检项标题")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, verbose_name="分类")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, verbose_name="巡检级别")
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='warn', verbose_name="默认严重度")
    # 4NF: applicable_db_types/references 两个独立多值集合已拆为子表
    description = models.TextField(blank=True, default='', verbose_name="描述")
    detect_sql = models.TextField(blank=True, default='', verbose_name="检测SQL")
    detect_method = models.CharField(max_length=100, blank=True, default='', verbose_name="检测方法",
        help_text="如 detect_high_blevel_index")
    threshold = models.JSONField(default=dict, verbose_name="阈值配置",
        help_text="如 {warn: 5, error: 10, critical: 20}")
    recommendation = models.TextField(blank=True, default='', verbose_name="修复建议")
    auto_fixable = models.BooleanField(default=False, verbose_name="是否可自动修复")
    auto_fix_sql = models.TextField(blank=True, default='', verbose_name="自动修复SQL")
    est_inspect_time_sec = models.IntegerField(default=10, verbose_name="预计耗时(秒)")
    is_enabled = models.BooleanField(default=True, verbose_name="是否启用")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def applicable_db_types(self):
        return [o.value for o in self.dbtype_set.order_by('id')]
    @applicable_db_types.setter
    def applicable_db_types(self, values):
        _replace_scalar_children(self, InspectionItemDbType, values, fk_field='item')
    @property
    def references(self):
        return [o.value for o in self.reference_set.order_by('id')]
    @references.setter
    def references(self, values):
        _replace_scalar_children(self, InspectionItemReference, values, fk_field='item')

    def __str__(self):
        return f"[{self.get_level_display()}] {self.item_id}: {self.title}"

    class Meta:
        verbose_name = "巡检项定义"
        verbose_name_plural = "巡检项定义"
        ordering = ['level', 'category', 'item_id']
        indexes = [
            models.Index(fields=['level', 'is_enabled']),
            models.Index(fields=['category']),
        ]


class InspectionItemDbType(models.Model):
    """4NF 子表：巡检项-适用数据库类型（多值拆分）"""
    item = models.ForeignKey(InspectionItem, on_delete=models.CASCADE, related_name='dbtype_set')
    value = models.CharField(max_length=20)
    class Meta:
        unique_together = [('item', 'value')]


class InspectionItemReference(models.Model):
    """4NF 子表：巡检项-参考链接（多值拆分）"""
    item = models.ForeignKey(InspectionItem, on_delete=models.CASCADE, related_name='reference_set')
    value = models.TextField()
    class Meta:
        unique_together = [('item', 'value')]


class InspectionRun(models.Model):
    """巡检执行记录"""
    STATUS_CHOICES = (
        ('running', '执行中'),
        ('success', '成功'),
        ('partial', '部分成功'),
        ('failed', '失败'),
    )
    LEVEL_CHOICES = InspectionItem.LEVEL_CHOICES

    run_id = models.CharField(max_length=64, unique=True, verbose_name="执行ID")
    db_config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
        related_name='inspection_runs', verbose_name="数据库")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, verbose_name="巡检级别")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running', verbose_name="状态")
    started_at = models.DateTimeField(verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration_sec = models.FloatField(null=True, blank=True, verbose_name="耗时(秒)")
    total_items = models.IntegerField(default=0, verbose_name="总项数")
    executed_items = models.IntegerField(default=0, verbose_name="已执行项数")
    passed_items = models.IntegerField(default=0, verbose_name="通过项数")
    failed_items = models.IntegerField(default=0, verbose_name="失败项数")
    error_items = models.IntegerField(default=0, verbose_name="错误项数")
    critical_count = models.IntegerField(default=0, verbose_name="严重问题数")
    error_count = models.IntegerField(default=0, verbose_name="错误问题数")
    warn_count = models.IntegerField(default=0, verbose_name="警告问题数")
    info_count = models.IntegerField(default=0, verbose_name="信息提示数")
    total_risk_score = models.FloatField(default=0.0, verbose_name="总风险评分")
    summary = models.JSONField(default=dict, verbose_name="汇总数据")
    error_message = models.TextField(blank=True, default='', verbose_name="错误信息")
    triggered_by = models.CharField(max_length=50, default='scheduler',
        verbose_name="触发方式", help_text="scheduler/manual/api")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    def __str__(self):
        return f"{self.run_id} | {self.db_config.name} | {self.level} | {self.status}"

    class Meta:
        verbose_name = "巡检执行"
        verbose_name_plural = "巡检执行列表"
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['db_config', '-started_at']),
            models.Index(fields=['status']),
        ]


class InspectionFinding(models.Model):
    """巡检发现的具体问题"""
    run = models.ForeignKey(InspectionRun, on_delete=models.CASCADE,
        related_name='findings', verbose_name="巡检执行")
    item = models.ForeignKey(InspectionItem, on_delete=models.CASCADE,
        related_name='findings', null=True, blank=True, verbose_name="巡检项")
    item_code = models.CharField(max_length=64, verbose_name="巡检项ID")
    title = models.CharField(max_length=200, verbose_name="问题标题")
    category = models.CharField(max_length=30, verbose_name="分类")
    severity = models.CharField(max_length=20, verbose_name="严重程度")
    risk_score = models.FloatField(default=0.0, verbose_name="风险评分")
    raw_data = models.JSONField(default=dict, verbose_name="原始检测数据")
    threshold_violated = models.JSONField(default=dict, verbose_name="违反的阈值")
    recommendation = models.TextField(blank=True, default='', verbose_name="修复建议")
    auto_fixable = models.BooleanField(default=False, verbose_name="是否可自动修复")
    status = models.CharField(max_length=20, default='open', verbose_name="状态",
        help_text="open/auto_fixed/manual_fixed/ignored/closed")
    auto_fixed = models.BooleanField(default=False, verbose_name="是否已自动修复")
    fix_record = models.TextField(blank=True, default='', verbose_name="修复记录")
    related_object = models.CharField(max_length=200, blank=True, default='', verbose_name="关联对象")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "巡检发现"
        verbose_name_plural = "巡检发现列表"
        ordering = ['-risk_score', '-create_time']
        indexes = [
            models.Index(fields=['run', 'severity']),
            models.Index(fields=['status']),
        ]


class InspectionIssuePattern(models.Model):
    """巡检问题模式识别 - 用于预测"""
    pattern_signature = models.CharField(max_length=128, unique=True, verbose_name="模式签名")
    description = models.CharField(max_length=200, verbose_name="模式描述")
    category = models.CharField(max_length=30, verbose_name="分类")
    occurrence_count = models.IntegerField(default=1, verbose_name="发生次数")
    first_seen = models.DateTimeField(verbose_name="首次发现")
    last_seen = models.DateTimeField(verbose_name="最近发现")
    last_db_config = models.ForeignKey(DatabaseConfig, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="最近发生数据库")
    recommended_action = models.TextField(blank=True, default='', verbose_name="推荐处理")
    auto_resolve_possible = models.BooleanField(default=False, verbose_name="可自动修复")
    severity = models.CharField(max_length=20, default='warn', verbose_name="典型严重度")
    avg_risk_score = models.FloatField(default=0.0, verbose_name="平均风险评分")
    sample_item_id = models.CharField(max_length=64, blank=True, default='', verbose_name="样本巡检项")

    def __str__(self):
        return f"{self.pattern_signature}: {self.description} (x{self.occurrence_count})"

    class Meta:
        verbose_name = "巡检问题模式"
        verbose_name_plural = "巡检问题模式列表"
        ordering = ['-occurrence_count']


# ==========================================================================
# Phase 6A: 事件-事故-问题模型 (Event / Incident / Problem)
# 详细设计: phase6/10_phase6A_discovery.md §1
# ==========================================================================
import uuid as _uuid


def _uid(prefix: str) -> str:
    return f"{prefix}-{_uuid.uuid4().hex[:12]}"


class Event(models.Model):
    """事件 - 机器视角, 高频, 允许风暴。检测层产出的原始信号。"""
    SOURCE_CHOICES = (
        ('sentinel', '哨兵'), ('collector', '采集器'), ('baseline', '基线'),
        ('ml', '机器学习'), ('inspection', '巡检'),
    )
    SEVERITY_CHOICES = (
        ('critical', '严重'), ('error', '错误'), ('warning', '警告'), ('info', '信息'),
    )
    event_uid = models.CharField(max_length=32, unique=True, db_index=True, verbose_name="事件UID")
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
        related_name='events', verbose_name="数据库")
    db_type = models.CharField(max_length=20, verbose_name="数据库类型")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, verbose_name="来源")
    signal = models.CharField(max_length=40, db_index=True, verbose_name="信号")
    metric_key = models.CharField(max_length=100, blank=True, default='', verbose_name="指标键")
    value = models.FloatField(default=0.0, verbose_name="值")
    threshold = models.FloatField(default=0.0, verbose_name="阈值")
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, db_index=True, verbose_name="严重度")
    dedup_key = models.CharField(max_length=120, db_index=True, verbose_name="去重键")
    occurred_at = models.DateTimeField(db_index=True, verbose_name="发生时刻")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="入库时刻")
    incident = models.ForeignKey('Incident', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='events', verbose_name="归属事故")
    detail = models.JSONField(default=dict, verbose_name="证据")

    class Meta:
        verbose_name = "事件"
        verbose_name_plural = "事件列表"
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['config', 'signal', 'occurred_at']),
            models.Index(fields=['dedup_key', 'occurred_at']),
        ]

    def __str__(self):
        return f"{self.event_uid} {self.signal}({self.severity})"


class Incident(models.Model):
    """事故 - 运维视角, 聚合后的处理单元, 一等公民。"""
    CATEGORY_CHOICES = (
        ('availability', '可用性'), ('lock', '锁'), ('connection', '连接'),
        ('capacity', '容量'), ('replication', '复制'), ('performance', '性能'),
        ('config', '配置'), ('other', '其他'),
    )
    PRIORITY_CHOICES = (('P1', 'P1'), ('P2', 'P2'), ('P3', 'P3'), ('P4', 'P4'))
    STATUS_CHOICES = (
        ('open', '待处理'), ('diagnosing', '诊断中'), ('plan_ready', '方案就绪'),
        ('executing', '执行中'), ('verifying', '验证中'), ('resolved', '已解决'),
        ('closed', '已关闭'),
    )
    # 合法状态转移 (phase6/10 §4.2)
    ALLOWED_TRANSITIONS = {
        'open': {'diagnosing', 'resolved', 'closed'},
        'diagnosing': {'plan_ready', 'resolved', 'closed'},
        'plan_ready': {'executing', 'resolved', 'closed'},
        'executing': {'verifying', 'resolved', 'closed', 'plan_ready'},
        'verifying': {'resolved', 'plan_ready', 'closed'},
        'resolved': {'open', 'closed'},
        'closed': set(),
    }

    incident_id = models.CharField(max_length=48, unique=True, db_index=True, verbose_name="事故ID")
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
        related_name='incidents', verbose_name="数据库")
    db_type = models.CharField(max_length=20, verbose_name="数据库类型")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True, verbose_name="类别")
    title = models.CharField(max_length=200, verbose_name="标题")
    priority = models.CharField(max_length=4, choices=PRIORITY_CHOICES, db_index=True, verbose_name="优先级")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True, default='open', verbose_name="状态")
    dedup_key = models.CharField(max_length=120, db_index=True, verbose_name="聚合键")
    event_count = models.IntegerField(default=1, verbose_name="事件数")
    is_storm = models.BooleanField(default=False, verbose_name="事件风暴")
    is_flapping = models.BooleanField(default=False, verbose_name="抖动")
    occurred_at = models.DateTimeField(db_index=True, verbose_name="发生时刻")
    detected_at = models.DateTimeField(null=True, blank=True, verbose_name="发现时刻")
    plan_ready_at = models.DateTimeField(null=True, blank=True, verbose_name="方案就位时刻")
    acked_at = models.DateTimeField(null=True, blank=True, verbose_name="确认时刻")
    acked_by = models.CharField(max_length=50, default='', blank=True, verbose_name="确认人")
    executing_at = models.DateTimeField(null=True, blank=True, verbose_name="执行时刻")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="解决时刻")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="关闭时刻")
    rca_result = models.JSONField(default=dict, verbose_name="根因诊断")
    impact = models.JSONField(default=dict, verbose_name="影响评估")
    # 4NF: plans 多值已拆为 IncidentPlan 子表（rca_result/impact 为 1:1 依赖文档，保留）
    health_snapshot = models.FloatField(default=0.0, verbose_name="生成时健康分")
    problem = models.ForeignKey('Problem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='incidents', verbose_name="关联问题")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "事故"
        verbose_name_plural = "事故列表"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['config', 'category', 'status']),
        ]

    @property
    def plans(self):
        return _read_ordered_children(self, 'plan_items')
    @plans.setter
    def plans(self, values):
        _replace_ordered_children(self, IncidentPlan, values, fk_field='incident')

    def __str__(self):
        return f"{self.incident_id} [{self.priority}] {self.title}"

    # ---- SLA 秒表 (phase6/README §3) ----
    @staticmethod
    def _delta(a, b):
        if a and b:
            return round((a - b).total_seconds(), 1)
        return None

    @property
    def t_detect_sec(self):
        return self._delta(self.detected_at, self.occurred_at)

    @property
    def t_plan_sec(self):
        return self._delta(self.plan_ready_at, self.detected_at)

    @property
    def t_resolve_sec(self):
        return self._delta(self.resolved_at, self.detected_at)

    @property
    def sla_detect_ok(self):
        v = self.t_detect_sec
        return v is not None and v <= 60

    @property
    def sla_plan_ok(self):
        v = self.t_plan_sec
        return v is not None and v <= 300

    @property
    def sla_resolve_ok(self):
        v = self.t_resolve_sec
        return v is not None and v <= 600

    def transition(self, to_status: str, actor: str = 'system', reason: str = ''):
        """状态机转移。非法转移抛 IncidentStateError。写时间戳 + AuditLog。"""
        if to_status not in self.ALLOWED_TRANSITIONS.get(self.status, set()):
            raise IncidentStateError(
                f"非法状态转移: {self.status} -> {to_status} (事故 {self.incident_id})")
        now = timezone.now()
        stamp_map = {
            'diagnosing': 'detected_at', 'plan_ready': 'plan_ready_at',
            'executing': 'executing_at', 'resolved': 'resolved_at', 'closed': 'closed_at',
        }
        field = stamp_map.get(to_status)
        if field and getattr(self, field) is None:
            setattr(self, field, now)
        old = self.status
        self.status = to_status
        self.save(update_fields=['status', field, 'updated_at'] if field else ['status', 'updated_at'])
        try:
            # AuditLog 字段: description/sql_command 必填(空串即可), executor 而非 operator
            AuditLog.objects.create(
                config=self.config,
                action_type='incident_transition',
                risk_level='low',
                status='success',
                description=f"事故 {self.incident_id}: {old} -> {to_status} by {actor}. {reason}".strip(),
                sql_command='',
                executor=actor,
            )
        except Exception:
            pass  # 审计失败不阻断状态机
        # Phase 8B: resolved 时异步蒸馏案例 (失败由每小时补偿扫描兜底)
        if to_status in ('resolved', 'closed'):
            try:
                from monitor.tasks_phase8 import dispatch_distill_incident
                dispatch_distill_incident(self.incident_id)
            except Exception:
                pass
        return self


class IncidentStateError(Exception):
    """事故状态机非法转移"""
    pass


class Problem(models.Model):
    """问题 - 跨事故共性根因, 沉淀知识。"""
    STATUS_CHOICES = (('active', '活跃'), ('mitigated', '已缓解'), ('archived', '已归档'))
    problem_id = models.CharField(max_length=48, unique=True, db_index=True, verbose_name="问题ID")
    signature = models.CharField(max_length=200, db_index=True, verbose_name="归一签名")
    title = models.CharField(max_length=200, verbose_name="标题")
    incident_count = models.IntegerField(default=0, verbose_name="累计事故数")
    first_seen_at = models.DateTimeField(null=True, blank=True, verbose_name="首次出现")
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name="最近出现")
    kb_ref = models.CharField(max_length=64, default='', blank=True, verbose_name="知识库引用")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', verbose_name="状态")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "问题"
        verbose_name_plural = "问题列表"
        ordering = ['-last_seen_at']

    def __str__(self):
        return f"{self.problem_id} {self.title} (x{self.incident_count})"


# ==========================================================================
# Phase 6C: Playbook / PlaybookRun / OnCallSchedule (phase6/30 §1)
# ==========================================================================
class Playbook(models.Model):
    """处置剧本 (对标 EM13c Corrective Actions)。"""
    RISK_CHOICES = (('low', '低'), ('mid', '中'), ('high', '高'))
    playbook_id = models.CharField(max_length=48, unique=True, db_index=True, verbose_name="剧本ID")
    name = models.CharField(max_length=200, verbose_name="名称")
    category = models.CharField(max_length=20, choices=Incident.CATEGORY_CHOICES, db_index=True, verbose_name="类别")
    signal = models.CharField(max_length=40, db_index=True, blank=True, default='', verbose_name="适用信号")
    # 4NF: applicable_db_types/precheck/steps/rollback 多值已拆为子表
    # (verify/params_schema 为 1:1 依赖文档，保留)
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, verbose_name="风险级")
    verify = models.JSONField(default=dict, verbose_name="验证判据")
    params_schema = models.JSONField(default=dict, verbose_name="参数定义")
    est_minutes = models.IntegerField(default=5, verbose_name="预计耗时(分)")
    enabled = models.BooleanField(default=True, verbose_name="启用")
    auto_execute = models.BooleanField(default=False, verbose_name="允许自动执行")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    @property
    def applicable_db_types(self):
        return [o.value for o in self.dbtype_set.order_by('id')]
    @applicable_db_types.setter
    def applicable_db_types(self, values):
        _replace_scalar_children(self, PlaybookDbType, values, fk_field='playbook')
    @property
    def precheck(self):
        return _read_ordered_children(self, 'step_items', phase='precheck')
    @precheck.setter
    def precheck(self, values):
        _replace_ordered_children(self, PlaybookStep, values, fk_field='playbook', phase='precheck')
    @property
    def steps(self):
        return _read_ordered_children(self, 'step_items', phase='steps')
    @steps.setter
    def steps(self, values):
        _replace_ordered_children(self, PlaybookStep, values, fk_field='playbook', phase='steps')
    @property
    def rollback(self):
        return _read_ordered_children(self, 'step_items', phase='rollback')
    @rollback.setter
    def rollback(self, values):
        _replace_ordered_children(self, PlaybookStep, values, fk_field='playbook', phase='rollback')

    class Meta:
        verbose_name = "处置剧本"
        verbose_name_plural = "处置剧本列表"

    def __str__(self):
        return f"{self.playbook_id} [{self.risk_level}] {self.name}"


class PlaybookRun(models.Model):
    """剧本执行实例。"""
    STATUS_CHOICES = (
        ('pending_approval', '待审批'), ('prechecking', '前置检查'), ('executing', '执行中'),
        ('verifying', '验证中'), ('succeeded', '成功'), ('failed', '失败'),
        ('rolled_back', '已回滚'), ('timeout', '验证超时'),
    )
    TRIGGER_CHOICES = (('auto', '自动'), ('one_click', '一键'), ('approved', '审批通过'))
    run_id = models.CharField(max_length=48, unique=True, db_index=True, verbose_name="执行ID")
    playbook = models.ForeignKey(Playbook, on_delete=models.PROTECT, related_name='runs', verbose_name="剧本")
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name='playbook_runs', verbose_name="事故")
    params = models.JSONField(default=dict, verbose_name="实际参数")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True, default='pending_approval', verbose_name="状态")
    trigger_mode = models.CharField(max_length=12, choices=TRIGGER_CHOICES, verbose_name="触发方式")
    approved_by = models.CharField(max_length=50, default='', blank=True, verbose_name="审批人")
    # 4NF: step_results 多值已拆为 PlaybookRunStepResult 子表（verify_result 为 1:1 文档，保留）
    verify_result = models.JSONField(default=dict, verbose_name="验证结果")
    error_message = models.TextField(blank=True, default='', verbose_name="错误信息")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    @property
    def step_results(self):
        return _read_ordered_children(self, 'step_result_items')
    @step_results.setter
    def step_results(self, values):
        _replace_ordered_children(self, PlaybookRunStepResult, values, fk_field='run')

    class Meta:
        verbose_name = "剧本执行"
        verbose_name_plural = "剧本执行列表"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.run_id} {self.status}"


class OnCallSchedule(models.Model):
    """值班表 (支柱六)。"""
    name = models.CharField(max_length=100, verbose_name="班次名")
    user = models.CharField(max_length=50, verbose_name="值班人")
    contact_dingtalk = models.CharField(max_length=120, default='', blank=True, verbose_name="钉钉")
    contact_wecom = models.CharField(max_length=120, default='', blank=True, verbose_name="企微")
    contact_phone = models.CharField(max_length=120, default='', blank=True, verbose_name="电话")
    weekday_mask = models.IntegerField(default=127, verbose_name="值班星期位掩码", help_text="bit0=周一")
    start_hour = models.IntegerField(default=0, verbose_name="值班开始小时")
    end_hour = models.IntegerField(default=24, verbose_name="值班结束小时")
    escalate_to = models.CharField(max_length=50, default='', blank=True, verbose_name="升级联系人")
    enabled = models.BooleanField(default=True, verbose_name="启用")

    class Meta:
        verbose_name = "值班表"
        verbose_name_plural = "值班表列表"

    def __str__(self):
        return f"{self.name}: {self.user}"


# ==========================================================================
# Phase 7A-08: SQL 执行计划库 (phase7/10 §9)
# ==========================================================================
class SqlPlan(models.Model):
    """SQL 执行计划快照。同一 digest 的最新计划 is_current=True (唯一)。"""
    SOURCE_CHOICES = (('auto', '自动'), ('manual', '手动'), ('incident', '事故'))
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
        related_name='sql_plans', verbose_name="数据库")
    sql_digest = models.CharField(max_length=32, db_index=True, verbose_name="SQL指纹")
    plan_hash = models.CharField(max_length=32, verbose_name="计划指纹")
    plan_json = models.JSONField(default=dict, verbose_name="计划(JSON)")
    plan_text = models.TextField(blank=True, default='', verbose_name="计划(文本)")
    cost_total = models.FloatField(null=True, blank=True, verbose_name="总代价")
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default='auto', verbose_name="来源")
    captured_at = models.DateTimeField(auto_now_add=True, verbose_name="采集时间")
    is_current = models.BooleanField(default=True, verbose_name="当前计划")

    class Meta:
        verbose_name = "SQL执行计划"
        verbose_name_plural = "SQL执行计划列表"
        ordering = ['-captured_at']
        indexes = [models.Index(fields=['config', 'sql_digest', 'is_current'])]
        constraints = [
            # BUG-119: 类文档一直声称 "is_current=True (唯一)"，但此前没有任何
            # 机制保证它。仅靠 select_for_update 不够 —— 行锁锁不住"尚不存在的行"
            # (幻读)：T1 把旧行置 false 并插入新行后，阻塞在旧行上的 T2 醒来时
            # 谓词已不匹配，它既看不到旧行也看不到 T1 刚插入的新行，于是再插一条
            # is_current=True。唯一约束在数据库层面把这个不变量钉死。
            models.UniqueConstraint(
                fields=['config', 'sql_digest'],
                condition=models.Q(is_current=True),
                name='uniq_current_plan_per_digest',
            ),
        ]

    def __str__(self):
        return f"{self.sql_digest[:12]}@{self.plan_hash[:8]}"


# ==========================================================================
# Phase 8: AI 智能诊断 (phase8/40)
# ==========================================================================
class LLMCallLog(models.Model):
    """LLM 调用留痕 (8A)。每次 chat/embed 调用一条, 用于审计/成本/质量分析。"""
    SCENE_CHOICES = (
        ('diagnosis', '事故诊断'), ('plan_draft', '方案草拟'), ('distill', '案例蒸馏'),
        ('agent', '主动排查'), ('embed', '向量化'), ('test', '连通测试'),
    )
    STATUS_CHOICES = (
        ('ok', '成功'), ('timeout', '超时'), ('unavailable', '不可用'),
        ('bad_json', '输出不合法'), ('error', '其他错误'),
    )
    scene = models.CharField(max_length=12, choices=SCENE_CHOICES, db_index=True, verbose_name="场景")
    incident_id = models.CharField(max_length=48, default='', blank=True, db_index=True, verbose_name="关联事故")
    provider = models.CharField(max_length=20, default='', blank=True, verbose_name="Provider")
    model = models.CharField(max_length=64, default='', blank=True, verbose_name="模型")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, db_index=True, verbose_name="状态")
    latency_ms = models.IntegerField(default=0, verbose_name="耗时(ms)")
    prompt_tokens = models.IntegerField(default=0, verbose_name="提示词token")
    completion_tokens = models.IntegerField(default=0, verbose_name="补全token")
    prompt_chars = models.IntegerField(default=0, verbose_name="提示词字符数")
    error_message = models.TextField(blank=True, default='', verbose_name="错误信息")
    response_digest = models.CharField(max_length=32, default='', blank=True, verbose_name="响应摘要指纹")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "LLM调用日志"
        verbose_name_plural = "LLM调用日志"
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.scene}] {self.status} {self.latency_ms}ms"


class RcaFeedback(models.Model):
    """根因反馈 (8B)。DBA 对单条根因假设的确认/否定, 驱动规则校准与案例蒸馏。"""
    VERDICT_CHOICES = (('correct', '准确'), ('wrong', '不准'), ('partial', '部分准确'))
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name='rca_feedbacks', verbose_name="事故")
    rule_id = models.CharField(max_length=24, db_index=True, verbose_name="规则/假设ID")
    source = models.CharField(max_length=12, default='rules', verbose_name="根因来源", help_text="rules/llm/both")
    verdict = models.CharField(max_length=12, choices=VERDICT_CHOICES, verbose_name="结论")
    actual_cause = models.TextField(blank=True, default='', verbose_name="实际根因(否定时填)")
    user = models.CharField(max_length=50, verbose_name="反馈人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "根因反馈"
        verbose_name_plural = "根因反馈列表"
        constraints = [models.UniqueConstraint(fields=['incident', 'rule_id', 'user'], name='uniq_rcafb_inc_rule_user')]

    def __str__(self):
        return f"{self.incident_id}/{self.rule_id}: {self.verdict}"


class PlanFeedback(models.Model):
    """方案反馈 (8B)。方案是否被采纳/有效。"""
    VERDICT_CHOICES = (('adopted', '采纳且有效'), ('adopted_failed', '采纳但无效'), ('rejected', '未采纳'))
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name='plan_feedbacks', verbose_name="事故")
    scenario = models.CharField(max_length=20, verbose_name="方案场景", help_text="conservative/standard/aggressive/llm_advisory")
    verdict = models.CharField(max_length=16, choices=VERDICT_CHOICES, verbose_name="结论")
    comment = models.TextField(blank=True, default='', verbose_name="备注")
    user = models.CharField(max_length=50, verbose_name="反馈人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "方案反馈"
        verbose_name_plural = "方案反馈列表"
        constraints = [models.UniqueConstraint(fields=['incident', 'scenario', 'user'], name='uniq_planfb_inc_scen_user')]

    def __str__(self):
        return f"{self.incident_id}/{self.scenario}: {self.verdict}"


class RuleStat(models.Model):
    """规则准确率统计 (8B)。rule_calibrator 每日重算, 供 RCA 置信度校准。"""
    rule_id = models.CharField(max_length=24, primary_key=True, verbose_name="规则ID")
    hit_count = models.IntegerField(default=0, verbose_name="命中次数")
    correct_count = models.IntegerField(default=0, verbose_name="确认准确次数")
    wrong_count = models.IntegerField(default=0, verbose_name="确认不准次数")
    accuracy = models.FloatField(default=0.0, verbose_name="准确率")
    calibrated_base = models.FloatField(default=0.6, verbose_name="校准后基础置信度")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "规则统计"
        verbose_name_plural = "规则统计列表"

    def __str__(self):
        return f"{self.rule_id} acc={self.accuracy:.2f} base={self.calibrated_base:.2f}"


class AgentTrace(models.Model):
    """Agent 排查轨迹 (8C)。一次 investigate 一条, steps 存完整思考-行动-观察序列。"""
    STATUS_CHOICES = (('running', '运行中'), ('done', '完成'), ('failed', '失败'), ('budget_exceeded', '预算耗尽'))
    trace_id = models.CharField(max_length=48, unique=True, db_index=True, verbose_name="轨迹ID")
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name='agent_traces', verbose_name="事故")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='running', verbose_name="状态")
    # 4NF: steps 多值已拆为 AgentTraceStep 子表（conclusion 为 1:1 文档，保留）
    conclusion = models.JSONField(default=dict, verbose_name="最终结论")
    triggered_by = models.CharField(max_length=50, default='system', verbose_name="触发人")
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")

    @property
    def steps(self):
        return _read_ordered_children(self, 'step_items')
    @steps.setter
    def steps(self, values):
        _replace_ordered_children(self, AgentTraceStep, values, fk_field='trace')

    class Meta:
        verbose_name = "Agent排查轨迹"
        verbose_name_plural = "Agent排查轨迹列表"
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.trace_id} {self.status} ({len(self.steps or [])} steps)"


class ChangeEvent(models.Model):
    """变更事件流 (8D)。统一汇聚参数漂移/DDL/人工登记三源, 供诊断关联。"""
    SOURCE_CHOICES = (('config_drift', '参数漂移'), ('ddl', 'DDL变更'), ('manual', '人工登记'), ('audit', '审计提取'))
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, related_name='change_events', verbose_name="数据库")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, db_index=True, verbose_name="来源")
    change_type = models.CharField(max_length=40, default='', blank=True, verbose_name="变更类型")
    title = models.CharField(max_length=200, verbose_name="标题")
    detail = models.JSONField(default=dict, verbose_name="详情", help_text="如 {param, old, new} 或 {ddl_text}")
    operator = models.CharField(max_length=50, default='', blank=True, verbose_name="操作人")
    dedup_key = models.CharField(max_length=64, unique=True, verbose_name="去重键")
    occurred_at = models.DateTimeField(db_index=True, verbose_name="发生时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="入库时间")

    class Meta:
        verbose_name = "变更事件"
        verbose_name_plural = "变更事件流"
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['config', 'occurred_at'])]

    def __str__(self):
        return f"[{self.source}] {self.title}"


class CausalEdge(models.Model):
    """挖掘出的因果边 (8D)。causal_miner 滞后互相关产出, 供 RCA 因果链增强。"""
    config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE, related_name='causal_edges', verbose_name="数据库")
    cause_metric = models.CharField(max_length=64, verbose_name="因指标")
    effect_metric = models.CharField(max_length=64, verbose_name="果指标")
    lag_minutes = models.IntegerField(default=5, verbose_name="滞后(分)")
    strength = models.FloatField(default=0.0, verbose_name="强度(相关系数)")
    sample_days = models.IntegerField(default=7, verbose_name="样本天数")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "因果边"
        verbose_name_plural = "因果边列表"
        constraints = [models.UniqueConstraint(fields=['config', 'cause_metric', 'effect_metric'], name='uniq_causal_cfg_cause_effect')]

    def __str__(self):
        return f"{self.cause_metric} --{self.lag_minutes}m--> {self.effect_metric} ({self.strength:.2f})"


# ==========================================================================
# 4NF 有序/复合多值子表（plans/steps/precheck/rollback/step_results 拆分）
# ==========================================================================
class IncidentPlan(models.Model):
    """4NF 子表：事故-处置方案（有序多值拆分）"""
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name='plan_items')
    seq = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    class Meta:
        unique_together = [('incident', 'seq')]
        ordering = ['seq']


class PlaybookDbType(models.Model):
    """4NF 子表：剧本-适用数据库类型（多值拆分）"""
    playbook = models.ForeignKey(Playbook, on_delete=models.CASCADE, related_name='dbtype_set')
    value = models.CharField(max_length=20)
    class Meta:
        unique_together = [('playbook', 'value')]


class PlaybookStep(models.Model):
    """4NF 子表：剧本-步骤（phase=precheck/steps/rollback，有序多值拆分）"""
    playbook = models.ForeignKey(Playbook, on_delete=models.CASCADE, related_name='step_items')
    phase = models.CharField(max_length=12)
    seq = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    class Meta:
        unique_together = [('playbook', 'phase', 'seq')]
        ordering = ['phase', 'seq']


class RemediationPlanStep(models.Model):
    """4NF 子表：处置方案-执行步骤（有序多值拆分）"""
    plan = models.ForeignKey(RemediationPlan, on_delete=models.CASCADE, related_name='step_items')
    seq = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    class Meta:
        unique_together = [('plan', 'seq')]
        ordering = ['seq']


class PlaybookRunStepResult(models.Model):
    """4NF 子表：剧本执行-步骤结果（有序多值拆分）"""
    run = models.ForeignKey(PlaybookRun, on_delete=models.CASCADE, related_name='step_result_items')
    seq = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    class Meta:
        unique_together = [('run', 'seq')]
        ordering = ['seq']


class AgentTraceStep(models.Model):
    """4NF 子表：Agent轨迹-步骤（有序多值拆分）"""
    trace = models.ForeignKey(AgentTrace, on_delete=models.CASCADE, related_name='step_items')
    seq = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    class Meta:
        unique_together = [('trace', 'seq')]
        ordering = ['seq']


class ComponentHeartbeat(models.Model):
    """组件心跳（W4 自监控）。

    每个 (component, instance) 一行，upsert 更新，不做时序留存 ——
    历史趋势不是目标，"现在还活着吗"才是。行数上界 = 组件数 × 副本数，
    通常 < 20 行，无需分区或清理策略。
    """
    COMPONENT_CHOICES = (
        ('collector', '指标采集器'),
        ('sentinel', '哨兵/ASH采样'),
        ('pipeline', '事件流水线消费者'),
        ('notifier', '通知发送器'),
    )
    STATUS_CHOICES = (('up', '正常'), ('down', '失联'))

    component = models.CharField(max_length=32, choices=COMPONENT_CHOICES,
                                 verbose_name="组件")
    instance = models.CharField(max_length=128,
                                verbose_name="实例标识",
                                help_text="hostname:pid，区分多副本部署")
    last_beat_at = models.DateTimeField(db_index=True, verbose_name="最后心跳时间")
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='up',
                              verbose_name="状态")
    meta = models.JSONField(default=dict, blank=True, verbose_name="附加信息",
                            help_text="如采集实例数、队列积压等，仅供展示")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="首次上报")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "组件心跳"
        verbose_name_plural = "组件心跳列表"
        unique_together = [('component', 'instance')]
        indexes = [models.Index(fields=['status', 'last_beat_at'])]

    def __str__(self):
        return f"{self.get_component_display()}@{self.instance} ({self.status})"


# =============================================================================
# v2.0 4NF 扩展模型: 实例画像基线 / 因果推理链 / 自愈剧本模板与执行
# =============================================================================

class DatabaseMetricProfile(models.Model):
    """v2.0: 数据库实例多维画像与 168h 基线特征 (4NF)"""
    PROFILE_TYPE_CHOICES = (
        ('oltp', '高频联机交易 OLTP'),
        ('olap', '分析统计报表 OLAP'),
        ('mixed', '混合负载 MIXED'),
        ('batch', '夜间批处理 BATCH'),
    )

    config = models.OneToOneField(
        DatabaseConfig, on_delete=models.CASCADE,
        related_name='v2_profile', verbose_name="数据库配置"
    )
    profile_type = models.CharField(
        max_length=32, choices=PROFILE_TYPE_CHOICES, default='mixed',
        verbose_name="负载分类"
    )
    cpu_cores = models.IntegerField(null=True, blank=True, verbose_name="CPU核数")
    memory_gb = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name="内存(GB)")
    data_disk_gb = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="磁盘空间(GB)")
    max_qps = models.IntegerField(default=0, verbose_name="峰值QPS")
    peak_hours_json = models.JSONField(default=list, blank=True, verbose_name="高峰时间段(0-23)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "实例画像基线"
        verbose_name_plural = "实例画像基线列表"

    def __str__(self):
        return f"{self.config.name} Profile ({self.profile_type})"


class IncidentCauseChain(models.Model):
    """v2.0: 故障因果推理链明细表 (4NF)"""
    NODE_TYPE_CHOICES = (
        ('CHANGE', '变更事件/DDL'),
        ('SQL', 'SQL执行/全表扫'),
        ('RESOURCE', '资源耗尽/水位'),
        ('LOCK', '锁等待/阻塞源'),
        ('CLUSTER', '集群节点/复制延迟'),
    )

    incident = models.ForeignKey(
        'Incident', on_delete=models.CASCADE,
        related_name='cause_chains', verbose_name="关联事故"
    )
    step_seq = models.IntegerField(verbose_name="步骤序号")
    node_type = models.CharField(max_length=32, choices=NODE_TYPE_CHOICES, verbose_name="节点类型")
    node_name = models.CharField(max_length=128, verbose_name="节点名称")
    description = models.TextField(verbose_name="节点推理描述")
    evidence_refs = models.JSONField(default=list, blank=True, verbose_name="关联证据编号列表")
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1.000, verbose_name="置信度")
    metric_snapshot = models.JSONField(default=dict, blank=True, verbose_name="触发时指标快照")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "故障因果链节点"
        verbose_name_plural = "故障因果链节点列表"
        unique_together = [('incident', 'step_seq')]
        ordering = ['incident', 'step_seq']

    def __str__(self):
        return f"{self.incident.incident_id} [Step {self.step_seq}] {self.node_name}"


class PlaybookTemplate(models.Model):
    """v2.0: 自愈应急剧本模板"""
    RISK_LEVEL_CHOICES = (
        ('low', '低风险 (可自愈)'),
        ('medium', '中风险 (建议确认)'),
        ('high', '高风险 (需双人审批)'),
        ('critical', '极高风险 (强制阻断)'),
    )

    code = models.CharField(max_length=64, unique=True, verbose_name="剧本编码")
    name = models.CharField(max_length=128, verbose_name="剧本名称")
    db_types = models.JSONField(default=list, verbose_name="适用数据库类型")
    risk_level = models.CharField(max_length=16, choices=RISK_LEVEL_CHOICES, default='medium', verbose_name="风险等级")
    min_autonomy_level = models.IntegerField(default=1, verbose_name="最低自治等级")
    steps_payload = models.JSONField(default=list, verbose_name="执行步骤序列")
    rollback_payload = models.JSONField(default=list, blank=True, verbose_name="逆向回滚步骤")
    description = models.TextField(blank=True, verbose_name="剧本描述")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "自愈剧本模板"
        verbose_name_plural = "自愈剧本模板列表"

    def __str__(self):
        return f"{self.name} ({self.code})"


class PlaybookRunRecord(models.Model):
    """v2.0: 自愈剧本执行记录"""
    STATUS_CHOICES = (
        ('dryrun_passed', '预演通过'),
        ('dryrun_rejected', '预演驳回'),
        ('approved', '已审批待执行'),
        ('running', '正在执行'),
        ('success', '执行成功'),
        ('failed', '执行失败'),
        ('rolled_back', '已自动回滚'),
    )

    run_id = models.CharField(max_length=64, primary_key=True, verbose_name="执行ID")
    incident = models.ForeignKey(
        'Incident', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='playbook_run_records', verbose_name="关联事故"
    )
    template = models.ForeignKey(
        PlaybookTemplate, on_delete=models.PROTECT,
        related_name='run_records', verbose_name="关联剧本"
    )
    config = models.ForeignKey(
        DatabaseConfig, on_delete=models.CASCADE,
        related_name='playbook_run_records', verbose_name="目标数据库"
    )
    operator = models.CharField(max_length=64, verbose_name="操作人")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='dryrun_passed', verbose_name="执行状态")
    dryrun_result = models.JSONField(default=dict, blank=True, verbose_name="预演评估结果")
    execution_result = models.JSONField(default=dict, blank=True, verbose_name="执行产出结果")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "自愈执行记录"
        verbose_name_plural = "自愈执行记录列表"

    def __str__(self):
        return f"{self.run_id} - {self.template.name} ({self.status})"

