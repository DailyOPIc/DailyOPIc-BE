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


def test_dynamic_route_uses_path_template(isolated_app):
    """동적 경로 파라미터가 라벨에 누출되지 않는지 확인."""
    client, _registry = isolated_app
    # operation_id에 고유 값을 넣어도 path_template은 템플릿이어야 함
    resp = client.get("/v1/operations/unique-id-12345")
    metrics_resp = client.get("/metrics")
    text = metrics_resp.text
    assert "unique-id-12345" not in text or 'path_template="/v1/operations/{operation_id}"' in text


def test_unmatched_route_uses_unmatched_label(isolated_app):
    """등록되지 않은 경로는 raw path 대신 'unmatched'로 통합."""
    client, _registry = isolated_app
    client.get("/random-bot-scan-1234")
    client.get("/wp-admin/login.php")
    metrics_resp = client.get("/metrics")
    text = metrics_resp.text
    assert "random-bot-scan" not in text
    assert "wp-admin" not in text
    assert 'path_template="unmatched"' in text


def test_ai_api_duration_recorded_on_structured_call():
    """_structured() 호출 시 ai_api_duration histogram에 값이 기록되는지 확인."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.services import metrics as metrics_mod
    from app.services.ai import AIService

    registry = CollectorRegistry()
    old = metrics_mod.metrics
    metrics_mod.metrics = metrics_mod.create_metrics(registry)
    try:
        svc = AIService(api_key="test-key", model="gpt-4o", mock=False, repository=MagicMock())

        mock_response = MagicMock()
        mock_response.output_text = '{"predicted_level": "IL"}'
        mock_response.output = []
        mock_response.id = "resp_test"
        mock_response.usage = None

        svc._client = AsyncMock()
        svc._client.responses.create = AsyncMock(return_value=mock_response)

        from pydantic import BaseModel

        class DummySchema(BaseModel):
            predicted_level: str

        result = asyncio.get_event_loop().run_until_complete(
            svc._structured(instructions="test", input_text="test", schema=DummySchema)
        )
        assert result.payload.predicted_level == "IL"

        # ai_api_duration histogram에 실제 observation이 기록되었는지 확인
        from prometheus_client import generate_latest

        output = generate_latest(registry).decode()
        assert "dailyopic_ai_api_duration_seconds_count{" in output
    finally:
        metrics_mod.metrics = old


def test_ai_token_counter_incremented():
    """_log_usage()에서 토큰 Counter가 증가하는지 확인."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.services import metrics as metrics_mod
    from app.services.ai import AIService

    registry = CollectorRegistry()
    old = metrics_mod.metrics
    metrics_mod.metrics = metrics_mod.create_metrics(registry)
    try:
        svc = AIService(api_key="test-key", model="gpt-4o", mock=False, repository=MagicMock())

        # SimpleNamespace: _usage_metric()의 model_dump hasattr 체크를 우회
        mock_usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            input_tokens_details=None,
            output_tokens_details=None,
        )
        mock_response = SimpleNamespace(usage=mock_usage, id="resp_test")

        svc._log_usage(mock_response, "DummySchema")

        from prometheus_client import generate_latest

        output = generate_latest(registry).decode()
        assert 'type="input"' in output
        assert 'type="output"' in output
    finally:
        metrics_mod.metrics = old


def test_ai_api_error_records_duration_and_error():
    """_structured()에서 예외 발생 시 duration과 error 모두 기록되는지 확인."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.services import metrics as metrics_mod
    from app.services.ai import AIService, AIServiceUnavailable

    registry = CollectorRegistry()
    old = metrics_mod.metrics
    metrics_mod.metrics = metrics_mod.create_metrics(registry)
    try:
        svc = AIService(api_key="test-key", model="gpt-4o", mock=False, repository=MagicMock())
        svc._client = AsyncMock()
        svc._client.responses.create = AsyncMock(side_effect=TimeoutError("timeout"))

        from pydantic import BaseModel

        class DummySchema(BaseModel):
            value: str

        with pytest.raises(AIServiceUnavailable):
            asyncio.get_event_loop().run_until_complete(
                svc._structured(instructions="test", input_text="test", schema=DummySchema)
            )

        from prometheus_client import generate_latest

        output = generate_latest(registry).decode()
        assert "dailyopic_ai_api_duration_seconds_count{" in output
        assert 'error_type="TimeoutError"' in output
    finally:
        metrics_mod.metrics = old
