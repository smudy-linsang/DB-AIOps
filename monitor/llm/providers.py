# -*- coding: utf-8 -*-
"""
Phase 8A: LLM Provider (phase8/20 §2)。

OpenAI 兼容 Chat Completions / Embeddings 单协议, requests 直连不引入 SDK。
本地 Ollama (http://localhost:11434/v1) 与云 API 仅是 BASE_URL/KEY/MODEL 差异。

异常体系:
    LLMTimeout      -- 超时 (不重试, 交由调用方降级)
    LLMUnavailable  -- 连接失败/5xx/401 等
    LLMBadResponse  -- 响应体不符合协议
每次调用写 LLMCallLog 留痕。
"""
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from django.conf import settings

logger = logging.getLogger("monitor.llm")


class LLMError(Exception):
    """LLM 调用异常基类"""


class LLMTimeout(LLMError):
    """调用超时 (不重试)"""


class LLMUnavailable(LLMError):
    """服务不可用: 连接拒绝/5xx/鉴权失败"""


class LLMBadResponse(LLMError):
    """响应体不符合 OpenAI 协议"""


@dataclass
class ChatResult:
    content: str
    model: str = ''
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


def _log_call(scene: str, status: str, *, incident_id: str = '', model: str = '',
              latency_ms: int = 0, prompt_tokens: int = 0, completion_tokens: int = 0,
              prompt_chars: int = 0, error: str = '', response_text: str = ''):
    """写 LLMCallLog, 失败不阻断主流程。"""
    try:
        from monitor.models import LLMCallLog
        LLMCallLog.objects.create(
            scene=scene, status=status, incident_id=incident_id or '',
            provider=getattr(settings, 'LLM_PROVIDER', 'openai_compat'),
            model=model, latency_ms=int(latency_ms),
            prompt_tokens=int(prompt_tokens), completion_tokens=int(completion_tokens),
            prompt_chars=int(prompt_chars), error_message=(error or '')[:2000],
            response_digest=hashlib.md5(response_text.encode('utf-8')).hexdigest() if response_text else '',
        )
    except Exception:  # noqa: BLE001 留痕失败不影响诊断
        logger.debug("[llm] LLMCallLog 写入失败", exc_info=True)


class OpenAICompatProvider:
    """OpenAI 兼容 Provider。支持 HTTP / SOCKS5 本地代理转发 (如 http://127.0.0.1:7890)。"""

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None,
                 timeout: int = None, proxy_url: str = None):
        self.base_url = (base_url or getattr(settings, 'LLM_BASE_URL', '')).rstrip('/')
        self.api_key = api_key or getattr(settings, 'LLM_API_KEY', '')
        self.model = model or getattr(settings, 'LLM_MODEL', '')
        self.timeout = int(timeout or getattr(settings, 'LLM_TIMEOUT_SEC', 25))
        self.proxy_url = proxy_url or getattr(settings, 'LLM_PROXY_URL', '')

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict, timeout: int = None) -> dict:
        import requests
        url = f"{self.base_url}{path}"
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f"Bearer {self.api_key}"
        proxies = None
        if self.proxy_url:
            proxies = {
                'http': self.proxy_url,
                'https': self.proxy_url,
            }
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=timeout or self.timeout, proxies=proxies)
        except requests.Timeout as e:
            raise LLMTimeout(f"LLM 请求超时 (>{timeout or self.timeout}s): {url}") from e
        except requests.RequestException as e:
            raise LLMUnavailable(f"LLM 服务不可达: {e}") from e
        if resp.status_code in (401, 403):
            raise LLMUnavailable(f"LLM 鉴权失败: HTTP {resp.status_code}")
        if resp.status_code == 404:
            raise LLMUnavailable(f"LLM 端点不存在 (HTTP 404): {url} (请检查 Base URL 是否正确，如 Gemini 官方兼容端点为 https://generativelanguage.googleapis.com/v1beta/openai)")
        if resp.status_code >= 500:
            raise LLMUnavailable(f"LLM 服务端错误: HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise LLMBadResponse(f"LLM 非预期状态码: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise LLMBadResponse(f"LLM 响应非 JSON: {resp.text[:200]}") from e

    # ------------------------------------------------------------------
    def _is_gemini_native(self) -> bool:
        """判断是否为 Google Gemini 官方原生端点"""
        return 'generativelanguage.googleapis.com' in self.base_url and not self.base_url.endswith('/openai')

    def chat(self, messages: List[dict], *, temperature: float = None,
             max_tokens: int = None, json_mode: bool = True,
             scene: str = 'diagnosis', incident_id: str = '',
             timeout: int = None) -> ChatResult:
        """Chat Completions。支持 OpenAI 规范与 Gemini 原生 REST 规范。"""
        prompt_chars = sum(len(m.get('content') or '') for m in messages)
        t0 = time.time()

        # =========================================================
        # 1. Google Gemini 原生 REST 规范 (generativelanguage.googleapis.com)
        # =========================================================
        if self._is_gemini_native():
            # 格式化 contents
            contents = []
            system_instruction = None
            for m in messages:
                role = m.get('role', 'user')
                content_text = m.get('content', '')
                if role == 'system':
                    system_instruction = {'parts': [{'text': content_text}]}
                elif role in ('user', 'assistant'):
                    gemini_role = 'user' if role == 'user' else 'model'
                    contents.append({
                        'role': gemini_role,
                        'parts': [{'text': content_text}]
                    })

            gemini_payload = {'contents': contents}
            if system_instruction:
                gemini_payload['system_instruction'] = system_instruction

            gen_config = {}
            if temperature is not None:
                gen_config['temperature'] = float(temperature)
            if max_tokens:
                gen_config['maxOutputTokens'] = int(max_tokens)
            if json_mode:
                gen_config['responseMimeType'] = 'application/json'
            if gen_config:
                gemini_payload['generationConfig'] = gen_config

            # Gemini URL 格式: /models/{model}:generateContent
            path = f"/models/{self.model}:generateContent"
            import requests
            url = f"{self.base_url}{path}"
            headers = {'Content-Type': 'application/json'}
            if self.api_key:
                headers['x-goog-api-key'] = self.api_key
                headers['Authorization'] = f"Bearer {self.api_key}"

            proxies = None
            if self.proxy_url:
                proxies = {'http': self.proxy_url, 'https': self.proxy_url}

            try:
                resp = requests.post(url, json=gemini_payload, headers=headers,
                                     timeout=timeout or self.timeout, proxies=proxies)
            except requests.Timeout as e:
                raise LLMTimeout(f"Gemini API 请求超时 (>{timeout or self.timeout}s): {url}") from e
            except requests.RequestException as e:
                raise LLMUnavailable(f"Gemini API 服务不可达: {e}") from e

            if resp.status_code in (401, 403):
                raise LLMUnavailable(f"Gemini API 鉴权失败 (HTTP {resp.status_code}): 请检查 API Key 是否有效")
            if resp.status_code != 200:
                raise LLMBadResponse(f"Gemini API 非预期响应 (HTTP {resp.status_code}): {resp.text[:300]}")

            try:
                data = resp.json()
            except ValueError as e:
                raise LLMBadResponse(f"Gemini 响应非 JSON: {resp.text[:200]}") from e

            latency_ms = int((time.time() - t0) * 1000)
            try:
                cand = data['candidates'][0]
                content = cand['content']['parts'][0]['text'] or ''
            except (KeyError, IndexError, TypeError) as e:
                raise LLMBadResponse(f"Gemini 响应结构异常: {json.dumps(data)[:200]}") from e

            usage = data.get('usageMetadata') or {}
            result = ChatResult(
                content=content,
                model=data.get('modelVersion', self.model),
                prompt_tokens=int(usage.get('promptTokenCount') or 0),
                completion_tokens=int(usage.get('candidatesTokenCount') or 0),
                latency_ms=latency_ms,
                raw=data,
            )
            _log_call(scene, 'ok', incident_id=incident_id, model=result.model,
                      latency_ms=latency_ms, prompt_tokens=result.prompt_tokens,
                      completion_tokens=result.completion_tokens,
                      prompt_chars=prompt_chars, response_text=content)
            return result

        # =========================================================
        # 2. OpenAI 标准规范 (/chat/completions)
        # =========================================================
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature if temperature is not None
                           else float(getattr(settings, 'LLM_TEMPERATURE', 0.1)),
            'max_tokens': int(max_tokens or getattr(settings, 'LLM_MAX_TOKENS', 2048)),
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        try:
            data = self._post('/chat/completions', payload, timeout=timeout)
        except LLMTimeout as e:
            _log_call(scene, 'timeout', incident_id=incident_id, model=self.model,
                      latency_ms=(time.time() - t0) * 1000, prompt_chars=prompt_chars, error=str(e))
            raise
        except LLMUnavailable as e:
            _log_call(scene, 'unavailable', incident_id=incident_id, model=self.model,
                      latency_ms=(time.time() - t0) * 1000, prompt_chars=prompt_chars, error=str(e))
            raise
        except LLMBadResponse as e:
            _log_call(scene, 'error', incident_id=incident_id, model=self.model,
                      latency_ms=(time.time() - t0) * 1000, prompt_chars=prompt_chars, error=str(e))
            raise
        latency_ms = int((time.time() - t0) * 1000)
        try:
            content = data['choices'][0]['message']['content'] or ''
        except (KeyError, IndexError, TypeError) as e:
            _log_call(scene, 'error', incident_id=incident_id, model=self.model,
                      latency_ms=latency_ms, prompt_chars=prompt_chars,
                      error=f"choices 结构异常: {e}")
            raise LLMBadResponse(f"LLM 响应缺少 choices: {json.dumps(data)[:200]}") from e
        usage = data.get('usage') or {}
        result = ChatResult(
            content=content, model=data.get('model', self.model),
            prompt_tokens=int(usage.get('prompt_tokens') or 0),
            completion_tokens=int(usage.get('completion_tokens') or 0),
            latency_ms=latency_ms, raw=data,
        )
        _log_call(scene, 'ok', incident_id=incident_id, model=result.model,
                  latency_ms=latency_ms, prompt_tokens=result.prompt_tokens,
                  completion_tokens=result.completion_tokens,
                  prompt_chars=prompt_chars, response_text=content)
        return result

    # ------------------------------------------------------------------
    def embed(self, texts: List[str], *, scene: str = 'embed') -> List[List[float]]:
        """Embeddings。返回与 texts 等长的向量列表。"""
        if not texts:
            return []
        payload = {'model': self.model, 'input': texts}
        t0 = time.time()
        try:
            data = self._post('/embeddings', payload)
        except LLMError as e:
            _log_call(scene, 'unavailable' if isinstance(e, LLMUnavailable)
                      else ('timeout' if isinstance(e, LLMTimeout) else 'error'),
                      model=self.model, latency_ms=(time.time() - t0) * 1000, error=str(e))
            raise
        latency_ms = int((time.time() - t0) * 1000)
        try:
            items = sorted(data['data'], key=lambda d: d.get('index', 0))
            vectors = [it['embedding'] for it in items]
        except (KeyError, TypeError) as e:
            _log_call(scene, 'error', model=self.model, latency_ms=latency_ms,
                      error=f"embeddings 结构异常: {e}")
            raise LLMBadResponse("Embeddings 响应缺少 data/embedding") from e
        if len(vectors) != len(texts):
            raise LLMBadResponse(f"Embeddings 数量不符: {len(vectors)} != {len(texts)}")
        _log_call(scene, 'ok', model=self.model, latency_ms=latency_ms,
                  prompt_chars=sum(len(t) for t in texts))
        return vectors


# ----------------------------------------------------------------------
# 工厂 (线程安全懒加载)
# ----------------------------------------------------------------------
_lock = threading.Lock()
_chat_provider: Optional[OpenAICompatProvider] = None
_embed_provider: Optional[OpenAICompatProvider] = None


def get_chat_provider() -> OpenAICompatProvider:
    global _chat_provider
    with _lock:
        if _chat_provider is None:
            _chat_provider = OpenAICompatProvider()
        return _chat_provider


def get_embed_provider() -> OpenAICompatProvider:
    global _embed_provider
    with _lock:
        if _embed_provider is None:
            _embed_provider = OpenAICompatProvider(
                base_url=getattr(settings, 'EMBED_BASE_URL', ''),
                api_key=getattr(settings, 'EMBED_API_KEY', ''),
                model=getattr(settings, 'EMBED_MODEL', 'bge-m3'),
            )
        return _embed_provider


def reset_providers():
    """测试/配置热更新用。"""
    global _chat_provider, _embed_provider
    with _lock:
        _chat_provider = None
        _embed_provider = None
