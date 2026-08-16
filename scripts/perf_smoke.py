#!/usr/bin/env python3
"""无第三方依赖的 HTTP 并发性能冒烟；输出机器可读 JSON 并执行阈值门禁。"""
import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * pct) - 1)]


def request_once(url, timeout):
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            ok = 200 <= response.status < 400
            status = response.status
    except urllib.error.HTTPError as exc:
        ok, status = False, exc.code
    except Exception as exc:
        ok, status = False, type(exc).__name__
    return ok, status, (time.perf_counter() - started) * 1000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--requests', type=int, default=2000)
    parser.add_argument('--concurrency', type=int, default=50)
    parser.add_argument('--timeout', type=float, default=3.0)
    parser.add_argument('--max-p95-ms', type=float, default=500.0)
    parser.add_argument('--max-error-rate', type=float, default=0.0)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error('requests/concurrency 必须大于 0')

    for _ in range(min(20, args.concurrency)):
        request_once(args.url, args.timeout)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(
            lambda _: request_once(args.url, args.timeout), range(args.requests)))
    elapsed = time.perf_counter() - started
    latencies = [row[2] for row in results]
    errors = [row for row in results if not row[0]]
    error_rate = len(errors) / len(results)
    report = {
        'url': args.url,
        'requests': len(results),
        'concurrency': args.concurrency,
        'elapsed_sec': round(elapsed, 3),
        'throughput_rps': round(len(results) / elapsed, 2),
        'latency_ms': {
            'mean': round(statistics.fmean(latencies), 2),
            'p50': round(percentile(latencies, 0.50), 2),
            'p95': round(percentile(latencies, 0.95), 2),
            'p99': round(percentile(latencies, 0.99), 2),
            'max': round(max(latencies), 2),
        },
        'errors': len(errors),
        'error_rate': round(error_rate, 6),
        'error_samples': [row[1] for row in errors[:5]],
        'thresholds': {
            'max_p95_ms': args.max_p95_ms,
            'max_error_rate': args.max_error_rate,
        },
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report['latency_ms']['p95'] > args.max_p95_ms or error_rate > args.max_error_rate:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
