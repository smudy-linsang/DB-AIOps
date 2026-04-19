"""
密码加密工具（AES-256-GCM）v0.1.0

用法：
    from monitor.crypto import encrypt_password, decrypt_password

加密后的密文格式（Base64 编码，存入数据库）：
    enc:<base64(nonce + ciphertext + tag)>

未加密的明文（旧数据兼容）不含 "enc:" 前缀，直接原样返回。

密钥来源（优先级从高到低）：
    1. 环境变量  DB_MONITOR_SECRET_KEY
    2. settings.py 中的 DB_MONITOR_SECRET_KEY
    3. 派生自 Django SECRET_KEY（开发便利，生产不推荐）
"""

import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from django.conf import settings

_ENC_PREFIX = 'enc:'


def _get_key() -> bytes:
    """获取 32 字节的 AES-256 密钥"""
    raw = (
        os.environ.get('DB_MONITOR_SECRET_KEY')
        or getattr(settings, 'DB_MONITOR_SECRET_KEY', None)
    )
    if raw:
        key_bytes = raw.encode() if isinstance(raw, str) else raw
    else:
        # 兜底：从 Django SECRET_KEY 派生（仅开发环境使用）
        key_bytes = settings.SECRET_KEY.encode()

    # 用 SHA-256 把任意长度密钥规整为 32 字节
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(key_bytes)
    return digest.finalize()


def encrypt_password(plaintext: str) -> str:
    """
    加密密码，返回带 "enc:" 前缀的 Base64 字符串。
    若传入的已经是密文（以 "enc:" 开头）则直接原样返回（幂等）。
    """
    if not plaintext or plaintext.startswith(_ENC_PREFIX):
        return plaintext

    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce，GCM 推荐长度
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    encoded = base64.b64encode(nonce + ciphertext).decode()
    return _ENC_PREFIX + encoded


def decrypt_password(stored: str) -> str:
    """
    解密密码。
    - 若以 "enc:" 开头 → 解密后返回明文
    - 否则视为旧数据明文，直接返回（向后兼容）
    """
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored  # 明文兼容模式

    try:
        key = _get_key()
        raw = base64.b64decode(stored[len(_ENC_PREFIX):])
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None).decode()
    except Exception as e:
        raise ValueError(f"密码解密失败：{e}") from e


def is_encrypted(value: str) -> bool:
    """判断一个存储值是否已经加密"""
    return bool(value and value.startswith(_ENC_PREFIX))


# ============================================================================
# 密码轮换管理器（新增）
# ============================================================================

class PasswordRotationManager:
    """
    密码轮换管理器
    
    支持功能：
    - 检查密码是否需要轮换
    - 执行密码轮换
    - 记录密码轮换历史
    """
    
    def __init__(self, max_age_days: int = 90):
        """
        初始化密码轮换管理器
        
        Args:
            max_age_days: 密码最大使用天数，默认 90 天
        """
        self.max_age_days = max_age_days
    
    def should_rotate(self, config_id: int) -> bool:
        """
        检查指定数据库配置的密码是否需要轮换
        
        Args:
            config_id: 数据库配置 ID
            
        Returns:
            True 如果需要轮换，否则 False
        """
        from monitor.models import DatabaseConfig
        from django.utils import timezone
        from datetime import timedelta
        
        try:
            config = DatabaseConfig.objects.get(id=config_id)
        except DatabaseConfig.DoesNotExist:
            return False
        
        # 如果密码是明文（旧的），需要加密
        if not is_encrypted(config.password):
            return True
        
        # 检查密码最后更新时间（如果有该字段）
        if hasattr(config, 'password_updated_at') and config.password_updated_at:
            days_since_update = (timezone.now() - config.password_updated_at).days
            return days_since_update >= self.max_age_days
        
        # 如果没有更新时间字段，假设需要定期轮换
        # 这里可以添加基于创建时间的检查
        return True
    
    def rotate_password(self, config_id: int, new_password: str) -> dict:
        """
        执行密码轮换
        
        Args:
            config_id: 数据库配置 ID
            new_password: 新密码（明文）
            
        Returns:
            dict: 包含操作结果的字典
        """
        from monitor.models import DatabaseConfig, AuditLog
        from django.utils import timezone
        from datetime import timedelta
        import json
        
        result = {
            'success': False,
            'message': '',
            'config_id': config_id
        }
        
        try:
            config = DatabaseConfig.objects.get(id=config_id)
        except DatabaseConfig.DoesNotExist:
            result['message'] = f"数据库配置 ID {config_id} 不存在"
            return result
        
        # 验证新密码不为空
        if not new_password or new_password.strip() == '':
            result['message'] = "新密码不能为空"
            return result
        
        # 保存旧密码用于回滚
        old_password = config.password
        old_password_encrypted = is_encrypted(old_password)
        
        # 加密并保存新密码
        try:
            encrypted_password = encrypt_password(new_password)
            config.password = encrypted_password
            config.save(update_fields=['password', 'updated_at'])
        except Exception as e:
            result['message'] = f"密码加密或保存失败: {e}"
            return result
        
        # 记录轮换日志
        try:
            AuditLog.objects.create(
                config=config,
                action_type='ROTATE_PASSWORD',
                description=f"数据库 {config.name} 密码轮换",
                sql_command=f"-- 密码已轮换，新密码加密后存储",
                risk_level='medium',
                rollback_command=f"-- 回滚：将密码恢复为旧值\nUPDATE monitor_databaseconfig SET password='{old_password}' WHERE id={config_id}",
                status='success',
                execution_result=json.dumps({
                    'old_password_encrypted': old_password_encrypted,
                    'new_password_encrypted': is_encrypted(encrypted_password),
                    'rotated_at': str(timezone.now())
                })
            )
        except Exception as e:
            # 审计日志记录失败不影响主流程
            pass
        
        result['success'] = True
        result['message'] = f"数据库 {config.name} 密码轮换成功"
        return result
    
    def batch_rotate_check(self) -> list:
        """
        批量检查哪些数据库配置需要密码轮换
        
        Returns:
            list: 需要轮换的数据库配置 ID 列表
        """
        from monitor.models import DatabaseConfig
        
        configs = DatabaseConfig.objects.filter(is_active=True)
        needs_rotation = []
        
        for config in configs:
            if self.should_rotate(config.id):
                needs_rotation.append({
                    'id': config.id,
                    'name': config.name,
                    'db_type': config.db_type
                })
        
        return needs_rotation
