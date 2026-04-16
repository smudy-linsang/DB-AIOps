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
