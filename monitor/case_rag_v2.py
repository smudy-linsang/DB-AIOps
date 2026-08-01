# -*- coding: utf-8 -*-
"""
Phase 8A: 案例库 RAG 2.0 (phase8/20 §7)。

混合检索: ES kNN(向量) + BM25(文本) → RRF 融合 (w_knn=RAG_KNN_WEIGHT)。
降级链: EMBED 关闭/ES 不可用/无向量 → 退回旧 case_rag 词法检索。
写路径: index_case_to_es() 把 AlertCase 投影进 ES (PG 为权威源)。
"""
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("monitor.llm")

RRF_K = 60  # RRF 常数, 业界默认


def _case_text(case) -> str:
    """案例的可嵌入文本表示 (与检索 query 同构)。"""
    sig = case.symptom_signature
    sig_text = ''
    if isinstance(sig, dict):
        sig_text = ' '.join(f"{k}={v}" for k, v in list(sig.items())[:20])
    elif sig:
        sig_text = str(sig)[:500]
    tags = ' '.join(case.tags or [])
    return f"{case.db_type} {case.severity} {case.title}\n{sig_text}\n{tags}\n{(case.root_cause or '')[:500]}"


def embed_texts(texts):
    """向量化入口; EMBED 关闭或失败返回 None (调用方降级)。"""
    from monitor.llm import embed_enabled
    if not embed_enabled():
        return None
    try:
        from monitor.llm.providers import get_embed_provider
        return get_embed_provider().embed(texts)
    except Exception as e:
        logger.warning("[rag] embedding 失败, 降级词法检索: %s", e)
        return None


def index_case_to_es(case) -> bool:
    """单案例写入 ES (含向量)。成功置 embedding_indexed=True。"""
    from monitor.elasticsearch_engine import CASES_INDEX, get_es_client, init_cases_index
    client = get_es_client()
    if not client:
        return False
    vectors = embed_texts([_case_text(case)])
    doc = {
        'case_id': case.case_id, 'title': case.title, 'db_type': case.db_type,
        'tags': case.tags or [], 'severity': case.severity,
        'source': getattr(case, 'source', 'manual'),
        'symptom_text': _case_text(case),
        'root_cause': (case.root_cause or '')[:5000],
        'resolution': (case.resolution or '')[:5000],
        'success_count': case.success_count, 'confidence': case.confidence,
        'updated_at': timezone.now().isoformat(),
    }
    if vectors:
        doc['embedding'] = vectors[0]
    try:
        init_cases_index()
        client.index(index=CASES_INDEX, id=case.case_id, document=doc)
        if vectors and not case.embedding_indexed:
            case.embedding_indexed = True
            case.save(update_fields=['embedding_indexed'])
        return True
    except Exception as e:
        logger.warning("[rag] 案例 %s 写入 ES 失败: %s", case.case_id, e)
        return False


def _knn_search(client, query_vector, db_type: str, top_k: int):
    from monitor.elasticsearch_engine import CASES_INDEX
    body = {
        'knn': {'field': 'embedding', 'query_vector': query_vector,
                'k': top_k * 2, 'num_candidates': max(top_k * 10, 50)},
        'size': top_k * 2, '_source': True,
    }
    if db_type:
        body['knn']['filter'] = {'term': {'db_type': db_type}}
    resp = client.search(index=CASES_INDEX, body=body)
    return [(h['_id'], h['_source'], h.get('_score', 0)) for h in resp['hits']['hits']]


def _text_search(client, query: str, db_type: str, top_k: int):
    from monitor.elasticsearch_engine import CASES_INDEX
    must = [{'multi_match': {'query': query,
                             'fields': ['symptom_text^2', 'title^2', 'root_cause', 'resolution']}}]
    fltr = [{'term': {'db_type': db_type}}] if db_type else []
    body = {'query': {'bool': {'must': must, 'filter': fltr}}, 'size': top_k * 2}
    resp = client.search(index=CASES_INDEX, body=body)
    return [(h['_id'], h['_source'], h.get('_score', 0)) for h in resp['hits']['hits']]


def _rrf_fuse(knn_hits, text_hits, top_k: int):
    """RRF 融合: score = w*1/(K+rank_knn) + (1-w)*1/(K+rank_text)。"""
    w = float(getattr(settings, 'RAG_KNN_WEIGHT', 0.7))
    scores, sources = {}, {}
    for rank, (cid, src, _) in enumerate(knn_hits, 1):
        scores[cid] = scores.get(cid, 0) + w / (RRF_K + rank)
        sources[cid] = src
    for rank, (cid, src, _) in enumerate(text_hits, 1):
        scores[cid] = scores.get(cid, 0) + (1 - w) / (RRF_K + rank)
        sources.setdefault(cid, src)
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    if not ranked:
        return []
    max_s = ranked[0][1] or 1.0
    return [{'case_id': cid,
             'similarity': round(s / max_s, 3),  # 归一化到 0-1
             'title': sources[cid].get('title', ''),
             'root_cause': sources[cid].get('root_cause', ''),
             'fix': sources[cid].get('resolution', ''),
             'success': (sources[cid].get('success_count') or 0) > 0,
             'retrieval': 'hybrid'} for cid, s in ranked]


def search_cases_v2(symptom: str, db_type: str = '', top_k: int = None) -> list:
    """混合检索入口。返回 [{case_id, similarity, root_cause, fix, success, retrieval}]。

    任一环节失败 → 旧 case_rag 词法检索 (retrieval='lexical')。
    """
    top_k = int(top_k or getattr(settings, 'RAG_TOPK', 5))
    try:
        from monitor.elasticsearch_engine import get_es_client
        client = get_es_client()
        if client:
            qv_list = embed_texts([symptom])
            knn_hits = []
            if qv_list:
                try:
                    knn_hits = _knn_search(client, qv_list[0], db_type, top_k)
                except Exception as e:
                    logger.debug("[rag] kNN 检索失败: %s", e)
            text_hits = []
            try:
                text_hits = _text_search(client, symptom, db_type, top_k)
            except Exception as e:
                logger.debug("[rag] 文本检索失败: %s", e)
            if knn_hits or text_hits:
                return _rrf_fuse(knn_hits, text_hits, top_k)
    except Exception as e:
        logger.warning("[rag] 混合检索异常: %s", e)

    # 降级: 旧词法检索
    return _lexical_fallback(symptom, db_type, top_k)


def _lexical_fallback(symptom: str, db_type: str, top_k: int) -> list:
    try:
        from monitor.case_rag import search_cases
        res = search_cases(symptom, db_type=db_type, top_k=top_k)
        out = []
        for m in (getattr(res, 'matches', None) or []):
            out.append({'case_id': m.case_id, 'similarity': m.similarity,
                        'title': m.title, 'root_cause': m.root_cause,
                        'fix': m.resolution, 'success': m.success_count > 0,
                        'retrieval': 'lexical'})
        return out
    except Exception as e:
        logger.debug("[rag] 词法降级也失败: %s", e)
        return []


def backfill_all_cases(batch: int = 50) -> dict:
    """把 PG 中未索引的案例批量投影进 ES。返回统计。"""
    from monitor.models import AlertCase
    qs = AlertCase.objects.all().order_by('case_id')
    total, ok = 0, 0
    for case in qs.iterator(chunk_size=batch):
        total += 1
        if index_case_to_es(case):
            ok += 1
    return {'total': total, 'indexed': ok}
