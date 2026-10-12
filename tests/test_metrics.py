from __future__ import annotations

from prometheus_client import CollectorRegistry


def test_metrics_module_defines_all_metrics():
    from app.services.metrics import create_metrics

    registry = CollectorRegistry()
    m = create_metrics(registry)

    # Counter._name strips _total suffix; Histogram keeps full name
    assert m.http_requests_total._name == "dailyopic_http_requests"
    assert m.http_request_duration._name == "dailyopic_http_request_duration_seconds"
    assert m.ai_api_duration._name == "dailyopic_ai_api_duration_seconds"
    assert m.ai_api_errors._name == "dailyopic_ai_api_errors"
    assert m.ai_tokens._name == "dailyopic_ai_tokens"
    assert m.question_gen_retries._name == "dailyopic_question_generation_retries"
    assert m.question_gen_fallbacks._name == "dailyopic_question_generation_fallbacks"


def test_histogram_buckets_for_ai_api():
    from app.services.metrics import create_metrics

    registry = CollectorRegistry()
    m = create_metrics(registry)
    # AI API 호출은 수십 초까지 소요 가능 — 120초 버킷 포함 확인
    assert 120.0 in m.ai_api_duration._kwargs["buckets"]
