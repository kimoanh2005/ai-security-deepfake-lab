"""TV4: issue real HTTP requests only to the configured loopback sandbox."""
import os
from time import perf_counter
from urllib.parse import urlparse
import httpx
from app.services.lab_service import LabResult, percentile, log_security_event


async def measure_api(count, client_factory=None):
    base = os.getenv('SANDBOX_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Sandbox must be a configured HTTP loopback origin')
    rows = []
    factory = client_factory or (lambda: httpx.AsyncClient(base_url=base, timeout=10, follow_redirects=False, trust_env=False))
    async with factory() as client:
        for defense in (False, True):
            for index in range(count):
                started = perf_counter()
                try:
                    response = await client.post('/sandbox/echo', json={'defense': defense, 'message': 'lab probe'})
                    row = dict(defense=defense, index=index + 1, status=response.status_code,
                               remaining=response.headers.get('X-RateLimit-Remaining', ''),
                               retry_after=response.headers.get('Retry-After', '0'), error=None)
                except httpx.RequestError as exc:
                    row = dict(defense=defense, index=index + 1, status=None, remaining='', retry_after='0', error=type(exc).__name__)
                row['duration_ms'] = round((perf_counter() - started) * 1000, 3)
                rows.append(row)
                log_security_event('API_LAB_PROBE', action_taken=str(row['status'] or 'NETWORK_ERROR'), **row)
    metrics = {}
    for defense in (False, True):
        selected = [r for r in rows if r['defense'] == defense]
        durations = [r['duration_ms'] for r in selected if r['status'] is not None]
        metrics[str(defense).lower()] = dict(http_200=sum(r['status'] == 200 for r in selected),
            http_429=sum(r['status'] == 429 for r in selected), errors=sum(r['status'] not in (200, 429) for r in selected),
            p50_ms=percentile(durations, .5), p95_ms=percentile(durations, .95))
    return LabResult(lab='api_rate_limit', success=all(r['status'] in (200, 429) for r in rows), metrics=metrics, runs=rows)
