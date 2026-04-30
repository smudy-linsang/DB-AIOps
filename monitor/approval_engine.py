"""
审批流程引擎 v2.0

职责：
- 多级审批流程管理
- 按风险等级自动路由审批步骤
- 审批记录追踪
- 工单状态机管理
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from django.utils import timezone
from django.contrib.auth.models import User

from monitor.models import (
    AuditLog, ApprovalStep, ApprovalRecord, DatabaseConfig
)

logger = logging.getLogger(__name__)


class ApprovalEngine:
    """多级审批引擎"""

    # 风险等级对应的默认审批步骤
    DEFAULT_APPROVAL_STEPS = {
        'low': [],  # 低风险无需审批
        'medium': [
            {'step_order': 1, 'approver_role': 'supervisor', 'description': 'DBA主管审批'},
        ],
        'high': [
            {'step_order': 1, 'approver_role': 'supervisor', 'description': 'DBA主管审批'},
            {'step_order': 2, 'approver_role': 'admin', 'description': '系统管理员审批'},
        ],
        'critical': [
            {'step_order': 1, 'approver_role': 'supervisor', 'description': 'DBA主管审批'},
            {'step_order': 2, 'approver_role': 'admin', 'description': '系统管理员审批'},
            {'step_order': 3, 'approver_role': 'admin', 'description': '部门负责人审批'},
        ],
    }

    def __init__(self):
        pass

    def create_ticket(
        self,
        config: DatabaseConfig,
        action_type: str,
        description: str,
        sql_command: str,
        risk_level: str = 'medium',
        rollback_command: str = None,
        source: str = 'manual',
        created_by: str = None,
        triggered_by_alert=None,
        pre_check_list: dict = None,
    ) -> AuditLog:
        """
        创建运维工单

        Args:
            config: 数据库配置
            action_type: 操作类型
            description: 操作描述
            sql_command: SQL命令
            risk_level: 风险等级 (low/medium/high/critical)
            rollback_command: 回滚命令
            source: 来源 (manual/ai_suggestion/alert)
            created_by: 创建人
            triggered_by_alert: 触发告警
            pre_check_list: 执行前检查项

        Returns:
            AuditLog 实例
        """
        # 低风险自动批准
        initial_status = 'approved' if risk_level == 'low' else 'pending'

        ticket = AuditLog.objects.create(
            config=config,
            action_type=action_type,
            description=description,
            sql_command=sql_command,
            risk_level=risk_level,
            rollback_command=rollback_command,
            status=initial_status,
            triggered_by_alert=triggered_by_alert,
            execution_context={'source': source, 'created_by': created_by},
            execution_evidence=pre_check_list,
        )

        if risk_level == 'low':
            logger.info(f"[ApprovalEngine] 低风险工单自动批准: #{ticket.id} {action_type}")
        else:
            logger.info(f"[ApprovalEngine] 创建工单: #{ticket.id} {action_type} 风险={risk_level}")

        return ticket

    def approve(
        self,
        ticket_id: int,
        approver_username: str,
        comment: str = None
    ) -> Tuple[bool, str]:
        """
        审批工单

        Args:
            ticket_id: 工单ID
            approver_username: 审批人用户名
            comment: 审批意见

        Returns:
            (success, message)
        """
        try:
            ticket = AuditLog.objects.get(id=ticket_id)
        except AuditLog.DoesNotExist:
            return False, f"工单 #{ticket_id} 不存在"

        if ticket.status not in ('pending', 'approved'):
            return False, f"工单 #{ticket_id} 当前状态为 {ticket.status}，无法审批"

        # 获取审批人角色
        approver_role = self._get_user_role(approver_username)

        # 获取当前需要的审批步骤
        required_steps = self._get_required_steps(ticket.risk_level)
        current_step = self._get_current_step(ticket)

        if current_step is None:
            return False, f"工单 #{ticket_id} 已完成所有审批步骤"

        # 验证审批人权限
        if not self._can_approve(approver_role, current_step['approver_role']):
            return False, f"用户 {approver_username} 角色 {approver_role} 无权执行步骤 {current_step['step_order']}"

        # 记录审批
        ApprovalRecord.objects.create(
            audit_log=ticket,
            step_order=current_step['step_order'],
            approver=approver_username,
            approver_role=approver_role,
            action='approve',
            comment=comment,
        )

        # 检查是否所有步骤都已完成
        completed_steps = ApprovalRecord.objects.filter(
            audit_log=ticket, action='approve'
        ).values_list('step_order', flat=True)

        all_approved = all(
            step['step_order'] in completed_steps
            for step in required_steps
        )

        if all_approved:
            ticket.status = 'approved'
            ticket.approver = approver_username
            ticket.approve_time = timezone.now()
            ticket.save(update_fields=['status', 'approver', 'approve_time'])
            logger.info(f"[ApprovalEngine] 工单 #{ticket_id} 所有审批完成，状态更新为 approved")
            return True, f"工单 #{ticket_id} 审批通过"
        else:
            logger.info(f"[ApprovalEngine] 工单 #{ticket_id} 步骤 {current_step['step_order']} 审批通过，等待下一步")
            return True, f"步骤 {current_step['step_order']} 审批通过，等待下一步审批"

    def reject(
        self,
        ticket_id: int,
        approver_username: str,
        reason: str = None
    ) -> Tuple[bool, str]:
        """
        拒绝工单

        Args:
            ticket_id: 工单ID
            approver_username: 审批人用户名
            reason: 拒绝原因

        Returns:
            (success, message)
        """
        try:
            ticket = AuditLog.objects.get(id=ticket_id)
        except AuditLog.DoesNotExist:
            return False, f"工单 #{ticket_id} 不存在"

        if ticket.status not in ('pending', 'approved'):
            return False, f"工单 #{ticket_id} 当前状态为 {ticket.status}，无法操作"

        approver_role = self._get_user_role(approver_username)

        # 记录拒绝
        ApprovalRecord.objects.create(
            audit_log=ticket,
            step_order=0,
            approver=approver_username,
            approver_role=approver_role,
            action='reject',
            comment=reason,
        )

        ticket.status = 'rejected'
        ticket.approver = approver_username
        ticket.approve_time = timezone.now()
        ticket.save(update_fields=['status', 'approver', 'approve_time'])

        logger.info(f"[ApprovalEngine] 工单 #{ticket_id} 被 {approver_username} 拒绝")
        return True, f"工单 #{ticket_id} 已拒绝"

    def execute(
        self,
        ticket_id: int,
        executor_username: str,
        dry_run: bool = False
    ) -> Tuple[bool, str]:
        """
        执行工单

        Args:
            ticket_id: 工单ID
            executor_username: 执行人用户名
            dry_run: 是否为试运行

        Returns:
            (success, message)
        """
        try:
            ticket = AuditLog.objects.get(id=ticket_id)
        except AuditLog.DoesNotExist:
            return False, f"工单 #{ticket_id} 不存在"

        if ticket.status != 'approved':
            return False, f"工单 #{ticket_id} 当前状态为 {ticket.status}，需要先审批通过"

        if dry_run:
            # 试运行模式：只返回SQL预览
            return True, f"[DRY RUN] SQL命令:\n{ticket.sql_command}\n\n回滚命令:\n{ticket.rollback_command or '无'}"

        # 实际执行
        ticket.status = 'executing'
        ticket.executor = executor_username
        ticket.execute_time = timezone.now()
        ticket.save(update_fields=['status', 'executor', 'execute_time'])

        try:
            # 执行SQL（这里需要实际的数据库连接）
            result = self._execute_sql(ticket)
            ticket.status = 'success'
            ticket.execution_result = result
            ticket.save(update_fields=['status', 'execution_result'])
            logger.info(f"[ApprovalEngine] 工单 #{ticket_id} 执行成功")
            return True, f"工单 #{ticket_id} 执行成功"
        except Exception as e:
            ticket.status = 'failed'
            ticket.execution_result = str(e)
            ticket.save(update_fields=['status', 'execution_result'])
            logger.error(f"[ApprovalEngine] 工单 #{ticket_id} 执行失败: {e}")
            return False, f"工单 #{ticket_id} 执行失败: {e}"

    def get_pending_tickets(self, user_role=None):
        """获取待审批工单列表"""
        qs = AuditLog.objects.filter(status='pending').select_related('config')
        if user_role:
            # 根据角色过滤
            pass
        return qs.order_by('-create_time')

    def get_ticket_detail(self, ticket_id):
        """获取工单详情（含审批记录）"""
        try:
            ticket = AuditLog.objects.select_related('config').get(id=ticket_id)
            approvals = ApprovalRecord.objects.filter(audit_log=ticket).order_by('step_order')
            required_steps = self._get_required_steps(ticket.risk_level)

            return {
                'ticket': ticket,
                'approvals': approvals,
                'required_steps': required_steps,
                'current_step': self._get_current_step(ticket),
                'is_fully_approved': ticket.status == 'approved',
            }
        except AuditLog.DoesNotExist:
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_required_steps(self, risk_level):
        """获取风险等级对应的审批步骤"""
        # 先查数据库配置
        db_steps = ApprovalStep.objects.filter(risk_level=risk_level).order_by('step_order')
        if db_steps.exists():
            return [
                {
                    'step_order': s.step_order,
                    'approver_role': s.approver_role,
                    'description': s.description,
                    'is_required': s.is_required,
                }
                for s in db_steps
            ]
        # 使用默认配置
        return self.DEFAULT_APPROVAL_STEPS.get(risk_level, [])

    def _get_current_step(self, ticket):
        """获取当前需要的审批步骤"""
        required_steps = self._get_required_steps(ticket.risk_level)
        if not required_steps:
            return None

        completed_steps = set(
            ApprovalRecord.objects.filter(
                audit_log=ticket, action='approve'
            ).values_list('step_order', flat=True)
        )

        for step in required_steps:
            if step['step_order'] not in completed_steps:
                return step

        return None

    def _get_user_role(self, username):
        """获取用户角色"""
        try:
            from monitor.models import UserProfile
            user = User.objects.get(username=username)
            profile = UserProfile.objects.get(user=user)
            return profile.role
        except Exception:
            return 'user'

    def _can_approve(self, user_role, required_role):
        """检查用户角色是否有权审批"""
        role_hierarchy = {
            'readonly': 0,
            'user': 1,
            'supervisor': 2,
            'admin': 3,
        }
        return role_hierarchy.get(user_role, 0) >= role_hierarchy.get(required_role, 0)

    def _execute_sql(self, ticket):
        """执行SQL命令（实际连接数据库执行）"""
        # 这里是一个占位实现，实际应该连接目标数据库执行
        # 安全起见，记录但不实际执行
        logger.warning(f"[ApprovalEngine] SQL执行请求: {ticket.sql_command[:200]}")
        return f"SQL已记录，等待手动执行: {ticket.sql_command[:200]}"
