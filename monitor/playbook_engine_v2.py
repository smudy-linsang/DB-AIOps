# -*- coding: utf-8 -*-
"""
DB-AIOps v2.0: 自愈 Playbook 决策与安全沙箱 (Dry-Run) 引擎
=========================================================
提供标准化应急处置、Dry-Run 预演影响评估、安全阻断拦截与逆向回滚保障。
"""
import logging
import uuid
from typing import Dict, Any, List, Optional
from django.utils import timezone
from monitor.models import (
    Playbook, PlaybookRun, PlaybookTemplate, Incident, DatabaseConfig,
)

logger = logging.getLogger("monitor.playbook")

# 预置 Playbook 模板的种子数据已迁至 migration 0030（get_or_create，
# 保留 DBA 自定义）。此处曾有 DEFAULT_PLAYBOOKS 与 init_default_playbooks()，
# 因在预演读路径上被反复调用、会覆盖运维改动而下线（见 R25-12）；
# 整改后已无任何调用点，一并删除，避免让人以为运行期还有 bootstrap。


class PlaybookExecutor:
    """自愈 Playbook 预演与安全执行中枢"""

    PROTECTED_USERS = {'root', 'sys', 'system', 'rdsadmin', 'postgres', 'administrator'}
    PLAYBOOK_MAP = {
        'KILL_ROOT_BLOCKER': 'PB-LOCK-KILL-BLOCKER',
    }

    @classmethod
    def _resolve_params(cls, template_code, incident, supplied):
        """从服务端事故证据解析高风险目标，不信任浏览器提交的用户名/SID。"""
        params = dict(supplied or {})
        if template_code != 'KILL_ROOT_BLOCKER':
            return params, None
        if incident is None:
            return {}, '终止阻塞会话必须关联有效事故'
        blocker_id = None
        for event in incident.events.order_by('-occurred_at'):
            chains = (event.detail or {}).get('chains') or []
            if chains:
                blocker_id = chains[0].get('blocker') or chains[0].get('blocker_id')
                if blocker_id:
                    break
        if not blocker_id:
            return {}, '事故证据中没有可复核的根阻塞会话，拒绝执行'
        return {'blocker_id': str(blocker_id)}, None

    @classmethod
    def evaluate_dryrun(cls, template_code: str, config: DatabaseConfig, params: Dict[str, Any], incident: Optional[Incident] = None) -> Dict[str, Any]:
        """
        Dry-Run 预演安全评估
        """
        template = PlaybookTemplate.objects.filter(code=template_code, is_active=True).first()
        if not template:
            return {'status': 'REJECTED', 'reason': f'剧本 {template_code} 不存在或已停用'}

        # 1. 检查适用数据库类型
        if config.db_type not in template.db_types:
            return {'status': 'REJECTED', 'reason': f'该剧本不支持 {config.db_type} 类型数据库'}

        # 2. 高风险目标必须来自服务端事故证据，浏览器参数不能充当安全事实。
        resolved_params, reason = cls._resolve_params(template_code, incident, params)
        if reason:
            return {'status': 'REJECTED', 'reason': reason}

        # 3. 预演通过，输出影响面评估
        return {
            'status': 'PASSED',
            'template_code': template.code,
            'template_name': template.name,
            'risk_level': template.risk_level,
            'impact_summary': f"预演通过。将针对目标实例 [{config.name}] 执行 [{template.name}]；生效时间需以执行后验证为准",
            'affected_sessions_estimate': 1,
            'released_locks_estimate': None,
            'rollback_available': False if template_code == 'KILL_ROOT_BLOCKER'
                                  else bool(template.rollback_payload),
            'resolved_params': resolved_params,
            'evaluated_at': timezone.now().isoformat()
        }

    @classmethod
    def execute_playbook(cls, template_code: str, config: DatabaseConfig, operator: str, params: Dict[str, Any], incident: Optional[Incident] = None) -> Dict[str, Any]:
        """
        正式执行自愈剧本并生成审计流记录
        """
        dryrun = cls.evaluate_dryrun(template_code, config, params, incident)
        if dryrun.get('status') != 'PASSED':
            return {'status': 'failed', 'error': dryrun.get('reason')}

        if incident is None or incident.config_id != config.id:
            return {'status': 'failed', 'error': '执行必须关联目标实例内的有效事故'}

        playbook_id = cls.PLAYBOOK_MAP.get(template_code)
        playbook = Playbook.objects.filter(
            playbook_id=playbook_id, enabled=True).first() if playbook_id else None
        if playbook is None:
            return {
                'status': 'failed',
                'error': f'剧本 {template_code} 尚未发布到统一执行引擎，未执行任何动作',
            }
        if config.db_type not in playbook.applicable_db_types:
            return {'status': 'failed', 'error': f'剧本不支持 {config.db_type}'}

        from monitor.playbook_authz import decide_trigger, record_auto_action
        decision = decide_trigger(incident, playbook)
        run = PlaybookRun.objects.create(
            run_id=f"PBR-{uuid.uuid4().hex}",
            playbook=playbook,
            incident=incident,
            params=dryrun.get('resolved_params') or {},
            trigger_mode=decision['mode'],
            status='prechecking' if decision['execute_now'] else 'pending_approval',
            approved_by=operator if decision['execute_now'] else '',
        )
        if not decision['execute_now']:
            return {
                'run_id': run.run_id,
                'status': 'pending_approval',
                'message': decision['reason'],
            }

        record_auto_action(config.id)
        from monitor.playbook_engine import execute_run
        result = execute_run(run.run_id)
        result['run_id'] = run.run_id
        result.setdefault('message', '剧本已真实下发，进入结果验证阶段')
        return result
