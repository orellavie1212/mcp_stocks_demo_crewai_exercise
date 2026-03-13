"""
Unit tests for shared Pydantic models.

Run: python -m pytest tests/unit/test_models/ -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../packages/shared-models"))

import uuid
from datetime import datetime, timezone

from models import (
    AnalysisRequest, JobRecord, JobStatus, JobStatusResponse,
    ToolCallRecord, UsageRecord, PubSubMessage, GuardrailEvent,
    GuardrailLayer, GuardrailDecision,
)


class TestAnalysisRequest:
    def test_basic_request(self):
        req = AnalysisRequest(query="Analyse AAPL")
        assert req.query == "Analyse AAPL"
        assert req.symbols == []
        assert req.user_id == "anonymous"

    def test_symbols_uppercased(self):
        req = AnalysisRequest(query="test", symbols=["aapl", "nvda"])
        assert req.symbols == ["AAPL", "NVDA"]

    def test_symbols_stripped(self):
        req = AnalysisRequest(query="test", symbols=["  AAPL  ", "NVDA"])
        assert req.symbols == ["AAPL", "NVDA"]

    def test_query_too_short_fails(self):
        import pytest
        with pytest.raises(Exception):
            AnalysisRequest(query="x")

    def test_query_too_long_fails(self):
        import pytest
        with pytest.raises(Exception):
            AnalysisRequest(query="x" * 2001)


class TestJobRecord:
    def test_default_job_id_is_uuid(self):
        req = AnalysisRequest(query="Test query AAPL")
        job = JobRecord(request=req)
        uuid.UUID(job.job_id)  # Raises if invalid

    def test_default_status_is_pending(self):
        req = AnalysisRequest(query="Test query AAPL")
        job = JobRecord(request=req)
        assert job.status == JobStatus.PENDING

    def test_to_firestore_round_trip(self):
        req = AnalysisRequest(query="Test AAPL analysis", symbols=["AAPL"])
        job = JobRecord(request=req)
        data = job.to_firestore()
        restored = JobRecord.from_firestore(data)
        assert restored.job_id == job.job_id
        assert restored.status == job.status
        assert restored.request.query == job.request.query


class TestJobStatusResponse:
    def test_from_job_record_pending(self):
        req = AnalysisRequest(query="Test AAPL")
        job = JobRecord(request=req)
        response = JobStatusResponse.from_job_record(job)
        assert response.status == JobStatus.PENDING
        assert response.latency_seconds is None

    def test_from_job_record_completed_has_latency(self):
        req = AnalysisRequest(query="Test AAPL")
        job = JobRecord(
            request=req,
            status=JobStatus.COMPLETED,
            started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 1, 1, 0, 1, 30, tzinfo=timezone.utc),
        )
        response = JobStatusResponse.from_job_record(job)
        assert response.latency_seconds == 90.0


class TestUsageRecord:
    def test_add_usage(self):
        usage = UsageRecord()
        usage.add("gemini-2.5-flash", 1000, 500, 0.001)
        assert usage.total_input_tokens == 1000
        assert usage.total_output_tokens == 500
        assert usage.total_tokens == 1500
        assert usage.estimated_cost_usd == 0.001
        assert "gemini-2.5-flash" in usage.model_usage

    def test_accumulate_usage(self):
        usage = UsageRecord()
        usage.add("gemini-2.5-flash", 1000, 500, 0.001)
        usage.add("gemini-2.5-flash", 2000, 1000, 0.002)
        assert usage.total_tokens == 4500
        assert round(usage.estimated_cost_usd, 6) == 0.003


class TestPubSubMessage:
    def test_message_has_trace_id(self):
        req = AnalysisRequest(query="Test AAPL analysis")
        msg = PubSubMessage(job_id="test-id", request=req)
        assert len(msg.trace_id) > 0

    def test_message_serializable(self):
        import json
        req = AnalysisRequest(query="Test AAPL analysis")
        msg = PubSubMessage(job_id="test-id", request=req)
        data = msg.model_dump(mode="json")
        # Must be JSON-serializable
        json.dumps(data, default=str)
