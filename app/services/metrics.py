from __future__ import annotations

from typing import NamedTuple

from prometheus_client import CollectorRegistry, Counter, Histogram, REGISTRY


AI_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)


class Metrics(NamedTuple):
    http_requests_total: Counter
    http_request_duration: Histogram
    ai_api_duration: Histogram
    ai_api_errors: Counter
    ai_tokens: Counter
    question_gen_retries: Counter
    question_gen_fallbacks: Counter


def create_metrics(registry: CollectorRegistry = REGISTRY) -> Metrics:
    return Metrics(
        http_requests_total=Counter(
            "dailyopic_http_requests_total",
            "Total HTTP requests",
            ["method", "path_template", "status"],
            registry=registry,
        ),
        http_request_duration=Histogram(
            "dailyopic_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path_template"],
            registry=registry,
        ),
        ai_api_duration=Histogram(
            "dailyopic_ai_api_duration_seconds",
            "AI API call duration in seconds",
            ["model", "function"],
            buckets=AI_BUCKETS,
            registry=registry,
        ),
        ai_api_errors=Counter(
            "dailyopic_ai_api_errors_total",
            "AI API call errors",
            ["model", "function", "error_type"],
            registry=registry,
        ),
        ai_tokens=Counter(
            "dailyopic_ai_tokens_total",
            "AI API token usage",
            ["model", "function", "type"],
            registry=registry,
        ),
        question_gen_retries=Counter(
            "dailyopic_question_generation_retries_total",
            "Question generation retry count",
            ["mode", "stage"],
            registry=registry,
        ),
        question_gen_fallbacks=Counter(
            "dailyopic_question_generation_fallbacks_total",
            "Question generation fallback count",
            ["mode", "stage"],
            registry=registry,
        ),
    )


metrics = create_metrics()
