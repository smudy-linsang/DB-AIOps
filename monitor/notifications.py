"""
告警通知渠道模块 v0.1.0

支持：
- 邮件（SMTP，沿用现有配置）
- 钉钉自定义机器人 Webhook（新增）

settings.py 配置项（可选，不配置则对应渠道静默）：
    # 钉钉机器人 Webhook URL（留空表示禁用）
    DINGTALK_WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=xxx'
    # 钉钉加签密钥（可选，不填则不签名）
    DINGTALK_SECRET = 'SECxxxx'
"""

import hmac
import hashlib
import base64
import time
import urllib.parse
import urllib.request
import json
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


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
    url = webhook
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
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            if result.get('errcode', -1) == 0:
                logger.info(f"[DingTalk] 发送成功：{title}")
                return True
            else:
                logger.warning(f"[DingTalk] 返回错误：{result}")
                return False
    except Exception as e:
        logger.warning(f"[DingTalk] 发送失败：{e}")
        return False
