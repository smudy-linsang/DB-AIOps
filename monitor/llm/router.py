# -*- coding: utf-8 -*-
"""
DB-AIOps v2.0: 多大模型智能路由调度引擎 (LLM Smart Router & High-Availability Gateway)
================================================================================
核心能力：
1. 多凭据连接池管理 (Credentials Pooling)
2. 场景化智能分流 (Scene-Based Dispatching)
3. 429 智能避让与指数退避 (Rate Limit Cooldown)
4. 毫秒级链式容灾降级 (Failover Fallback Chains)
5. 自动留痕与决策调用轨迹追踪 (Trace Recording)
"""
import time
import logging
from datetime import timedelta
from typing import List, Dict, Any, Optional
from django.conf import settings
from django.utils import timezone
from monitor.models import LLMProviderCredential, LLMSceneRoutingRule
from monitor.llm.providers import OpenAICompatProvider, ChatResult, LLMError, LLMTimeout, LLMUnavailable

logger = logging.getLogger("monitor.llm.router")


class LLMAllProvidersExhausted(LLMError):
    """所有候选大模型凭据均耗尽/异常"""


class LLMRouterEngine:
    """v2.0: 企业级多大模型智能路由与容灾调度引擎"""

    @classmethod
    def get_candidate_chain(cls, scene_code: str) -> List[LLMProviderCredential]:
        """
        获取指定场景下的候选凭据链路 (Primary -> Fallbacks -> System Credential List)
        """
        rule = LLMSceneRoutingRule.objects.filter(scene_code=scene_code).first()
        candidates = []
        seen_ids = set()

        if rule:
            if rule.primary_credential and rule.primary_credential.is_active:
                candidates.append(rule.primary_credential)
                seen_ids.add(rule.primary_credential.id)

            for fb in rule.fallback_credentials.filter(is_active=True).order_by('priority', '-weight'):
                if fb.id not in seen_ids:
                    candidates.append(fb)
                    seen_ids.add(fb.id)

        # 若场景未配置特定凭据，则回退读取全局启用的所有凭据 (按优先级与权重排序)
        if not candidates:
            all_active = LLMProviderCredential.objects.filter(is_active=True).order_by('priority', '-weight')
            for c in all_active:
                if c.id not in seen_ids:
                    candidates.append(c)
                    seen_ids.add(c.id)

        return candidates

    @classmethod
    def chat(cls, messages: List[Dict[str, Any]], *, scene: str = 'copilot_chat',
             incident_id: str = '', json_mode: bool = False, **kwargs) -> Dict[str, Any]:
        """
        统一入口：带场景路由、健康检测、429 智能避让与 Failover 容灾降级的 Chat Completions
        """
        # 1. 查找场景超参定义
        rule = (LLMSceneRoutingRule.objects.filter(scene_code=scene).first()
                or LLMSceneRoutingRule.objects.filter(scene_code='global_default').first())
        temperature = kwargs.get('temperature') or (rule.temperature if rule else 0.1)
        max_tokens = kwargs.get('max_tokens') or (rule.max_tokens if rule else 2048)
        timeout_sec = kwargs.get('timeout') or (rule.timeout_sec if rule else 25)

        # 2. 获取候选凭据链
        candidates = cls.get_candidate_chain(scene)
        failover_traces = []
        now = timezone.now()

        # 3. 逐个尝试候选凭据
        for cred in candidates:
            # 过滤处于 429 冷却期或被禁用的凭据
            if not cred.is_active:
                continue
            if cred.cooldown_until and cred.cooldown_until > now:
                failover_traces.append({
                    'provider': cred.name,
                    'model': cred.model_name,
                    'status': 'skipped',
                    'reason': f"429 冷却避让中 (剩余 {(cred.cooldown_until - now).seconds}s)"
                })
                continue

            t0 = time.time()
            try:
                provider = OpenAICompatProvider(
                    base_url=cred.base_url,
                    api_key=cred.get_api_key(),
                    model=cred.model_name,
                    timeout=timeout_sec,
                )
                result: ChatResult = provider.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    scene=scene,
                    incident_id=incident_id,
                    timeout=timeout_sec
                )

                latency_ms = int((time.time() - t0) * 1000)

                # 恢复健康状态并记录时延
                cred.consecutive_fails = 0
                cred.is_healthy = True
                cred.last_latency_ms = latency_ms
                cred.last_error_message = ''
                cred.save(update_fields=['consecutive_fails', 'is_healthy', 'last_latency_ms', 'last_error_message'])

                return {
                    'content': result.content,
                    'model': result.model or cred.model_name,
                    'provider_name': cred.name,
                    'provider_type': cred.provider_type,
                    'latency_ms': latency_ms,
                    'failover_traces': failover_traces,
                    'source': 'llm_router'
                }

            except Exception as exc:
                latency_ms = int((time.time() - t0) * 1000)
                err_str = str(exc)
                failover_traces.append({
                    'provider': cred.name,
                    'model': cred.model_name,
                    'status': 'failed',
                    'error': err_str[:160],
                    'latency_ms': latency_ms
                })

                # 若触发 429 Rate Limit，进入 60 秒静默冷却
                if '429' in err_str:
                    cred.cooldown_until = now + timedelta(seconds=60)

                cred.consecutive_fails += 1
                if cred.consecutive_fails >= 3:
                    cred.is_healthy = False
                cred.last_error_message = err_str[:300]
                cred.save(update_fields=['cooldown_until', 'consecutive_fails', 'is_healthy', 'last_error_message'])

                logger.warning(
                    "[LLMRouter] 凭据 %s (%s) 调用异常，触发故障转移: %s",
                    cred.name, cred.model_name, exc
                )

        # 4. 如果数据库凭据链全部失败，尝试使用系统默认 settings (若存在)
        default_base = getattr(settings, 'LLM_BASE_URL', '')
        default_key = getattr(settings, 'LLM_API_KEY', '')
        default_model = getattr(settings, 'LLM_MODEL', '')
        if default_base and getattr(settings, 'LLM_ENABLED', False):
            try:
                t0 = time.time()
                provider = OpenAICompatProvider(
                    base_url=default_base,
                    api_key=default_key,
                    model=default_model,
                    timeout=timeout_sec
                )
                res = provider.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode, scene=scene)
                latency_ms = int((time.time() - t0) * 1000)
                return {
                    'content': res.content,
                    'model': res.model or default_model,
                    'provider_name': 'SystemDefaultSettings',
                    'provider_type': 'system',
                    'latency_ms': latency_ms,
                    'failover_traces': failover_traces,
                    'source': 'llm_router'
                }
            except Exception as e:
                failover_traces.append({
                    'provider': 'SystemDefaultSettings',
                    'model': default_model,
                    'status': 'failed',
                    'error': str(e)[:160]
                })

        raise LLMAllProvidersExhausted(
            f"所有候选大模型凭据均尝试失败，全链路熔断轨迹: {failover_traces}"
        )
