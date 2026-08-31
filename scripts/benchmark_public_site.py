"""Measure public-site latency and transferred bytes with persistent connections."""

from __future__ import annotations

import argparse
import http.client
import math
import ssl
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit


DEFAULT_PATHS = ("/", "/experience/", "/games/", "/community/")


@dataclass(frozen=True, slots=True)
class Sample:
    status: int
    elapsed_ms: float
    transferred_bytes: int
    encoding: str


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _connection(parsed, timeout: float):
    host = parsed.hostname
    if not host:
        raise ValueError("benchmark URL needs a hostname")
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(
            host,
            parsed.port or 443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    if parsed.scheme == "http":
        return http.client.HTTPConnection(host, parsed.port or 80, timeout=timeout)
    raise ValueError("benchmark URL scheme must be http or https")


def _request_target(base_path: str, path: str) -> str:
    if not path.startswith("/"):
        raise ValueError(f"benchmark path must start with '/': {path}")
    prefix = base_path.rstrip("/")
    return f"{prefix}{path}" if prefix else path


def _worker(
    parsed,
    paths: tuple[str, ...],
    request_indexes: range,
    timeout: float,
    accept_encoding: str,
) -> tuple[list[Sample], list[str]]:
    samples: list[Sample] = []
    errors: list[str] = []
    connection = None
    try:
        for index in request_indexes:
            target = _request_target(parsed.path, paths[index % len(paths)])
            if connection is None:
                connection = _connection(parsed, timeout)
            started = time.perf_counter()
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Accept-Encoding": accept_encoding,
                        "User-Agent": "tangtang-public-site-benchmark/1.0",
                    },
                )
                response = connection.getresponse()
                body = response.read()
                elapsed_ms = (time.perf_counter() - started) * 1000
                samples.append(
                    Sample(
                        status=response.status,
                        elapsed_ms=elapsed_ms,
                        transferred_bytes=len(body),
                        encoding=response.getheader("Content-Encoding") or "identity",
                    )
                )
                if response.will_close:
                    connection.close()
                    connection = None
            except (OSError, http.client.HTTPException) as exc:
                errors.append(f"{target}: {type(exc).__name__}: {exc}")
                if connection is not None:
                    connection.close()
                    connection = None
    finally:
        if connection is not None:
            connection.close()
    return samples, errors


def run_benchmark(
    url: str,
    paths: tuple[str, ...],
    request_count: int,
    concurrency: int,
    timeout: float,
    accept_encoding: str,
) -> tuple[list[Sample], list[str], float]:
    if request_count <= 0 or concurrency <= 0 or timeout <= 0:
        raise ValueError("requests, concurrency, and timeout must be positive")
    parsed = urlsplit(url)
    worker_count = min(concurrency, request_count)
    started = time.perf_counter()
    futures = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for worker_index in range(worker_count):
            indexes = range(worker_index, request_count, worker_count)
            futures.append(
                executor.submit(
                    _worker,
                    parsed,
                    paths,
                    indexes,
                    timeout,
                    accept_encoding,
                )
            )
    elapsed = time.perf_counter() - started
    samples: list[Sample] = []
    errors: list[str] = []
    for future in futures:
        worker_samples, worker_errors = future.result()
        samples.extend(worker_samples)
        errors.extend(worker_errors)
    return samples, errors, elapsed


def _counter_text(counter: Counter) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items(), key=lambda item: str(item[0])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18769")
    parser.add_argument("--path", action="append", dest="paths", help="repeatable request path")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--accept-encoding", default="br, gzip")
    parser.add_argument("--expect-status", type=int, default=200)
    args = parser.parse_args()
    paths = tuple(args.paths or DEFAULT_PATHS)

    try:
        samples, errors, elapsed = run_benchmark(
            args.url,
            paths,
            args.requests,
            args.concurrency,
            args.timeout_seconds,
            args.accept_encoding,
        )
    except ValueError as exc:
        parser.error(str(exc))

    latencies = [sample.elapsed_ms for sample in samples]
    statuses = Counter(sample.status for sample in samples)
    encodings = Counter(sample.encoding for sample in samples)
    transferred = sum(sample.transferred_bytes for sample in samples)
    throughput = len(samples) / elapsed if elapsed else 0.0
    mean_bytes = transferred / len(samples) if samples else 0.0
    print(f"target={args.url} paths={','.join(paths)}")
    print(
        f"completed={len(samples)}/{args.requests} concurrency={min(args.concurrency, args.requests)} "
        f"elapsed_s={elapsed:.3f} requests_per_s={throughput:.2f}"
    )
    print(
        f"latency_ms p50={_percentile(latencies, 0.50):.2f} "
        f"p95={_percentile(latencies, 0.95):.2f} "
        f"mean={statistics.fmean(latencies) if latencies else 0.0:.2f} "
        f"max={max(latencies, default=0.0):.2f}"
    )
    print(f"statuses {_counter_text(statuses)}")
    print(f"encodings {_counter_text(encodings)}")
    print(f"transfer_bytes total={transferred} mean={mean_bytes:.1f}")
    if errors:
        print(f"errors count={len(errors)} first={errors[0]}")
    return 0 if not errors and statuses == Counter({args.expect_status: args.requests}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
