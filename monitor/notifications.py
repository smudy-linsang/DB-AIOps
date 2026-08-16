"""
告警通知渠道模块 v1.0

支持：
- 邮件（SMTP，沿用现有配置）
- 钉钉自定义机器人 Webhook
- 企业微信机器人 Webhook（新增）
- 告警聚合（新增）

settings.py 配置项（可选，不配置则对应渠道静默）：
    # 钉钉机器人 Webhook URL（留空表示禁用）
    DINGTALK_WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=xxx'
    # 钉钉加签密钥（可选，不填则不签名）
    DINGTALK_SECRET = 'SECxxxx'
    # 企业微信机器人 Webhook（新增）
    WECOM_WEBHOOK = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
"""

import hmac
import hashlib
import base64
import time
import urllib.parse
import json
import logging

import requests

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

_CHANNEL_HOSTS = {
    'dingtalk': frozenset({'oapi.dingtalk.com'}),
    'wecom': frozenset({'qyapi.weixin.qq.com'}),
}
_WEBHOOK_MAX_RESPONSE_BYTES = 64 * 1024


def _validated_webhook_url(url: str, channel: str) -> str:
    """校验机器人地址，阻断 SSRF、降级传输和 URL 凭据注入。"""
    from monitor import appconf

    parsed = urllib.parse.urlsplit(url)
    allowed = set(appconf.get('WEBHOOK_ALLOWED_HOSTS')) & _CHANNEL_HOSTS[channel]
    host = (parsed.hostname or '').lower()
    if (parsed.scheme != 'https' or host not in allowed
            or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError(f'{channel} webhook 地址不符合安全策略')
    return url


def _note_webhook_failure(channel: str, exc: Exception) -> None:
    """URL 异常通常包含访问令牌，只记录类型而不记录异常文本。"""
    logger.warning('[%s] 发送失败: %s', channel, type(exc).__name__)
    from monitor import degrade
    degrade.note(f'notify.{channel}', reason=type(exc).__name__)


def _post_webhook(url: str, payload: dict) -> dict:
    """发送受限 Webhook：不跟随重定向、分段超时、响应最多 64 KiB。"""
    response = requests.post(
        url, json=payload, headers={'Content-Type': 'application/json; charset=utf-8'},
        timeout=(3, 5), allow_redirects=False, stream=True,
    )
    with response:
        if 300 <= response.status_code < 400:
            raise ValueError('webhook redirect is forbidden')
        response.raise_for_status()
        declared = response.headers.get('Content-Length')
        if declared and int(declared) > _WEBHOOK_MAX_RESPONSE_BYTES:
            raise ValueError('webhook response too large')
        body = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            body.extend(chunk)
            if len(body) > _WEBHOOK_MAX_RESPONSE_BYTES:
                raise ValueError('webhook response too large')
    return json.loads(body.decode('utf-8'))


# ------------------------------------------------------------------
# 邮件通知
# ------------------------------------------------------------------

def send_email_alert(title: str, body: str) -> bool:
    """发送邮件告警，返回是否成功"""
    recipients = getattr(settings, 'ADMIN_EMAILS', [])
    if not recipients:
        return False
    try:
        send_mail(
            subject=title,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        logger.info(f"[Email] 发送成功：{title}")
        return True
    except Exception as e:
        logger.warning(f"[Email] 发送失败：{e}")
        # W6-L2: "告警发不出去"必须可见 —— 计数供 /system/degradations 暴露
        from monitor import degrade
        degrade.note('notify.email', exc=e)
        return False


# ------------------------------------------------------------------
# 钉钉 Webhook 通知
# ------------------------------------------------------------------

def _dingtalk_sign(secret: str, timestamp: int) -> str:
    """生成钉钉机器人加签"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).digest()
    return urllib.parse.quote_plus(base64.b64encode(hmac_code))


def send_dingtalk_alert(title: str, body: str) -> bool:
    """
    发送钉钉自定义机器人 Webhook 通知。
    若 DINGTALK_WEBHOOK 未配置则静默跳过。
    """
    webhook = getattr(settings, 'DINGTALK_WEBHOOK', '').strip()
    if not webhook:
        return False

    secret = getattr(settings, 'DINGTALK_SECRET', '').strip()
    try:
        url = _validated_webhook_url(webhook, 'dingtalk')
    except (TypeError, ValueError) as e:
        _note_webhook_failure('dingtalk', e)
        return False
    if secret:
        timestamp = int(time.time() * 1000)
        sign = _dingtalk_sign(secret, timestamp)
        url = f"{webhook}&timestamp={timestamp}&sign={sign}"

    # 使用 Markdown 格式，钉钉支持
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': f"### {title}\n\n```\n{body}\n```",
        },
    }
    try:
        result = _post_webhook(url, payload)
        if result.get('errcode', -1) == 0:
            logger.info(f"[DingTalk] 发送成功：{title}")
            return True
        logger.warning("[DingTalk] 返回业务错误 errcode=%s", result.get('errcode'))
        from monitor import degrade
        degrade.note('notify.dingtalk', reason=f"errcode={result.get('errcode')}")
        return False
    except Exception as e:
        _note_webhook_failure('dingtalk', e)
        return False


# ------------------------------------------------------------------
# 企业微信 Webhook 通知（新增）
# ------------------------------------------------------------------

def send_wecom_alert(title: str, body: str, mentioned_mobile: str = None) -> bool:
    """
    发送企业微信自定义机器人 Webhook 通知。
    若 WECOM_WEBHOOK 未配置则静默跳过。
    
    Args:
        title: 消息标题
        body: 消息内容
        mentioned_mobile: 被 @ 的手机号（可选）
    """
    webhook = getattr(settings, 'WECOM_WEBHOOK', '').strip()
    if not webhook:
        return False
    try:
        webhook = _validated_webhook_url(webhook, 'wecom')
    except (TypeError, ValueError) as e:
        _note_webhook_failure('wecom', e)
        return False
    
    # 构建消息内容
    content = f"**{title}**\n\n{body}"
    
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'content': content
        }
    }
    
    # 如果指定了被 @ 的手机号
    if mentioned_mobile:
        payload['markdown']['mentioned_mobile_list'] = [mentioned_mobile]
    
    try:
        result = _post_webhook(webhook, payload)
        if result.get('errcode', -1) == 0:
            logger.info(f"[WeCom] 发送成功：{title}")
            return True
        logger.warning("[WeCom] 返回业务错误 errcode=%s", result.get('errcode'))
        from monitor import degrade
        degrade.note('notify.wecom', reason=f"errcode={result.get('errcode')}")
        return False
    except Exception as e:
        _note_webhook_failure('wecom', e)
        return False


# ------------------------------------------------------------------
# 告警聚合管理（新增）
# ------------------------------------------------------------------

class AlertAggregator:
    """
    告警聚合管理器
    
    功能：
    - 同一时间窗口内的同类告警聚合
    - 批量通知
    """
    
    def __init__(self, window_seconds: int = 300):
        """
        初始化聚合器
        
        Args:
            window_seconds: 聚合时间窗口（秒），默认 5 分钟
        """
        self.window_seconds = window_seconds
        self._alerts = {}  # {(alert_type, metric_key): [alerts]}
        self._last_flush = {}  # {(alert_type, metric_key): timestamp}
    
    def add_alert(self, alert) -> bool:
        """
        添加告警到聚合队列
        
        Args:
            alert: AlertLog 对象
            
        Returns:
            True 如果触发了聚合发送，False 如果只是加入队列
        """
        key = (alert.alert_type, alert.metric_key)
        
        if key not in self._alerts:
            self._alerts[key] = []
            self._last_flush[key] = time.time()
        
        self._alerts[key].append(alert)
        
        # 检查是否需要触发聚合发送
        elapsed = time.time() - self._last_flush[key]
        if elapsed >= self.window_seconds:
            self._flush(key)
            return True
        
        return False
    
    def _flush(self, key):
        """触发聚合发送"""
        alerts = self._alerts.pop(key, [])
        if not alerts:
            return
        
        # 聚合告警内容
        unique_configs = list(set(a.config.name for a in alerts))
        count = len(alerts)
        
        title = f"【聚合告警】{key[0]} - {key[1]}"
        body = f"检测到 {count} 个同类告警：\n"
        body += "\n".join(f"- {a.config.name}: {a.title}" for a in alerts[:5])
        if count > 5:
            body += f"\n... 还有 {count - 5} 个"
        
        # 发送聚合通知
        send_email_alert(title, body)
        send_dingtalk_alert(title, body)
        send_wecom_alert(title, body)
        
        self._last_flush[key] = time.time()
    
    def flush_all(self):
        """立即刷新所有聚合告警"""
        for key in list(self._alerts.keys()):
            self._flush(key)


# ------------------------------------------------------------------
# 统一通知入口
# ------------------------------------------------------------------

def send_alert_notification(alert) -> dict:
    """
    统一的通知入口，发送告警到所有已配置渠道
    
    Args:
        alert: AlertLog 对象
        
    Returns:
        dict: 包含各渠道发送结果的字典
    """
    # AlertLog.severity 无 choices, 无 get_severity_display; 用安全映射兜底
    sev_map = {'info': '提示', 'warning': '警告', 'error': '高危',
               'critical': '严重', 'emergency': '紧急'}
    sev_text = sev_map.get(getattr(alert, 'severity', ''), getattr(alert, 'severity', ''))
    title = f"[{sev_text}] {alert.title}"
    body = alert.description
    
    results = {
        'email': send_email_alert(title, body),
        'dingtalk': send_dingtalk_alert(title, body),
        'wecom': send_wecom_alert(title, body)
    }
    
    return results
