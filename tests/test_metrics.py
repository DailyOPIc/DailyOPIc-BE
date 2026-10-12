from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry
from fastapi.testclient import TestClient


@pytest.fixture()
def isolated_app():
    """prometheus 글로벌 레지스트리 대신 격리된 레지스트리로 FastAPI 앱 구성."""
    from app.services import metrics as metrics_mod

    registry = CollectorRegistry()
    old = metrics_mod.metrics
    metrics_mod.metrics = metrics_mod.create_metrics(registry)
    try:
        from app.main import app

        with TestClient(app) as client:
            yield client, registry
    finally:
        metrics_mod.metrics = old


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


def test_metrics_endpoint_returns_http_metrics(isolated_app):
    client, registry = isolated_app
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "dailyopic_http_requests_total" in resp.text
    assert 'path_template="/health"' in resp.text


def test_metrics_endpoint_not_self_instrumented(isolated_app):
    client, _registry = isolated_app
    client.get("/metrics")
    resp = client.get("/metrics")
    assert 'path_template="/metrics"' not in resp.text
