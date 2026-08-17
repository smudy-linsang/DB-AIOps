"""LLM 出站端点安全校验。

数据库中的端点属于 Web 可配置输入；部署白名单属于受控配置。两者必须在保存时和
真正发请求前重复校验，避免绕过 API 直接写库。
"""
import ipaddress
import socket
from urllib.parse import urlsplit

from django.conf import settings


class LLMEndpointValidationError(ValueError):
    """LLM 端点不满足出站安全策略。"""


def _allowed_hosts() -> set[str]:
    raw = getattr(settings, 'LLM_ALLOWED_ENDPOINT_HOSTS', ()) or ()
    if isinstance(raw, str):
        raw = raw.split(',')
    return {str(item).strip().lower().rstrip('.') for item in raw if str(item).strip()}


def _validate_resolved_addresses(hostname: str) -> None:
    if hostname in _allowed_hosts():
        return
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = {literal}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise LLMEndpointValidationError('LLM 端点域名无法解析') from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise LLMEndpointValidationError(
            'LLM 端点解析到私网、回环、链路本地或保留地址；内网端点须由部署白名单显式放行')


def validate_llm_base_url(value: str) -> str:
    """校验并返回规范化的 HTTPS LLM Base URL。"""
    value = (value or '').strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise LLMEndpointValidationError('LLM Base URL 格式非法') from exc
    if parsed.scheme.lower() != 'https':
        raise LLMEndpointValidationError('LLM Base URL 必须使用 HTTPS')
    if not parsed.hostname:
        raise LLMEndpointValidationError('LLM Base URL 缺少主机名')
    if parsed.username or parsed.password:
        raise LLMEndpointValidationError('LLM Base URL 禁止内嵌用户名或密码')
    if parsed.fragment:
        raise LLMEndpointValidationError('LLM Base URL 禁止携带 fragment')
    if port not in (None, 443):
        raise LLMEndpointValidationError('LLM Base URL 仅允许 443 端口')
    _validate_resolved_addresses(parsed.hostname.lower().rstrip('.'))
    return value


def validate_deployment_proxy_url(value: str) -> str:
    """校验部署期代理；Web API 不得写入此配置。"""
    value = (value or '').strip().rstrip('/')
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise LLMEndpointValidationError('LLM 代理 URL 格式非法') from exc
    if parsed.scheme.lower() != 'https':
        raise LLMEndpointValidationError('LLM 部署代理必须使用 HTTPS')
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise LLMEndpointValidationError('LLM 部署代理 URL 缺少主机或包含凭据/fragment')
    if port not in (None, 443):
        raise LLMEndpointValidationError('LLM 部署代理仅允许 443 端口')
    _validate_resolved_addresses(parsed.hostname.lower().rstrip('.'))
    return value
