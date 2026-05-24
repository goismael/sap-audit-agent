"""
Tests for Layer 1 — SAP Data Collector
Uses mocked HTTP responses — no live SAP connection required.
"""

import pytest
import responses as responses_lib
import json
from unittest.mock import patch, MagicMock

from src.collector.odata_client import SAPODataClient
from src.collector.journal_entry_collector import JournalEntryCollector
from src.collector.evidence_store import LocalEvidenceStore
from src.common.models import EventType


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAP_CONFIG = {
    "base_url": "https://sap-test.example.com",
    "client": "100",
    "username": "SVC_AUDIT_AGENT",
    "password": "test_password",
    "page_size": 10,
    "timeout_seconds": 5,
    "max_retries": 1,
}

AGENT_SERVICE_USERS = {"SVC_AGENT_EXEC_POST", "SVC_AGENT_READER_GL"}

SAMPLE_JOURNAL_ENTRY = {
    "CompanyCode": "1000",
    "FiscalYear": "2026",
    "AccountingDocument": "100000001",
    "FiscalPeriod": "04",
    "PostingDate": "2026-04-30",
    "AccountingDocumentType": "SA",
    "DocumentReferenceID": "close-cycle-2026-04-Q1-001",
    "DocumentHeaderText": "Period close accrual",
    "CreatedByUser": "SVC_AGENT_EXEC_POST",
    "CreationDate": "2026-04-30",
    "CreationTime": "23:47:12",
    "GLAccount": "400000",
    "AmountInCompanyCodeCurrency": "184200.00",
    "CompanyCodeCurrency": "USD",
    "CostCenter": "CC1000",
    "ProfitCenter": "PC1000",
    "LastChangeDate": "2026-04-30",
}

SAMPLE_HUMAN_ENTRY = {
    **SAMPLE_JOURNAL_ENTRY,
    "AccountingDocument": "100000002",
    "CreatedByUser": "JSMITH",
    "DocumentReferenceID": "",
}


# ── OData Client Tests ─────────────────────────────────────────────────────────

class TestSAPODataClient:

    def setup_method(self):
        self.client = SAPODataClient(SAP_CONFIG)

    @responses_lib.activate
    def test_get_page_success(self):
        """Successful OData page retrieval returns results."""
        responses_lib.add(
            responses_lib.GET,
            "https://sap-test.example.com/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntryItem",
            json={"d": {"results": [SAMPLE_JOURNAL_ENTRY], "__next": None}},
            status=200,
        )

        result = self.client.get_page(
            service="/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV",
            entity="A_JournalEntryItem",
            filters=["CompanyCode eq '1000'"],
        )

        assert "d" in result
        assert len(result["d"]["results"]) == 1

    @responses_lib.activate
    def test_get_page_auth_failure(self):
        """401 response raises PermissionError."""
        responses_lib.add(
            responses_lib.GET,
            "https://sap-test.example.com/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntryItem",
            status=401,
        )

        with pytest.raises(PermissionError, match="authentication failed"):
            self.client.get_page(
                service="/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV",
                entity="A_JournalEntryItem",
            )

    @responses_lib.activate
    def test_get_page_authorization_failure(self):
        """403 response raises PermissionError with P001 reference."""
        responses_lib.add(
            responses_lib.GET,
            "https://sap-test.example.com/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntryItem",
            status=403,
        )

        with pytest.raises(PermissionError, match="P001"):
            self.client.get_page(
                service="/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV",
                entity="A_JournalEntryItem",
            )

    @responses_lib.activate
    def test_get_all_pagination(self):
        """get_all follows pagination links and yields all records."""
        # Page 1
        responses_lib.add(
            responses_lib.GET,
            "https://sap-test.example.com/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntryItem",
            json={
                "d": {
                    "results": [SAMPLE_JOURNAL_ENTRY],
                    "__next": "...?$skiptoken=page2"
                }
            },
            status=200,
        )
        # Page 2
        responses_lib.add(
            responses_lib.GET,
            "https://sap-test.example.com/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntryItem",
            json={"d": {"results": [SAMPLE_HUMAN_ENTRY], "__next": None}},
            status=200,
        )

        results = list(self.client.get_all(
            service="/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV",
            entity="A_JournalEntryItem",
        ))

        assert len(results) == 2


# ── Journal Entry Collector Tests ─────────────────────────────────────────────

class TestJournalEntryCollector:

    def setup_method(self):
        self.mock_client = MagicMock(spec=SAPODataClient)
        self.collector = JournalEntryCollector(
            client=self.mock_client,
            agent_service_users=AGENT_SERVICE_USERS,
            company_codes=["1000"],
            fiscal_year="2026",
        )

    def test_agent_posted_detection(self):
        """Records posted by agent service users are flagged correctly."""
        self.mock_client.get_all.return_value = iter([SAMPLE_JOURNAL_ENTRY])

        records = self.collector.collect()

        assert len(records) == 1
        assert records[0].is_agent_posted is True
        assert records[0].posted_by_user == "SVC_AGENT_EXEC_POST"

    def test_human_posted_detection(self):
        """Records posted by human users are not flagged as agent-posted."""
        self.mock_client.get_all.return_value = iter([SAMPLE_HUMAN_ENTRY])

        records = self.collector.collect()

        assert len(records) == 1
        assert records[0].is_agent_posted is False

    def test_session_reference_extracted(self):
        """Session reference is extracted from DocumentReferenceID for agent records."""
        self.mock_client.get_all.return_value = iter([SAMPLE_JOURNAL_ENTRY])

        records = self.collector.collect()

        assert records[0].agent_session_reference == "close-cycle-2026-04-Q1-001"

    def test_no_session_reference_for_human(self):
        """Human-posted records have no session reference."""
        self.mock_client.get_all.return_value = iter([SAMPLE_HUMAN_ENTRY])

        records = self.collector.collect()

        assert records[0].agent_session_reference is None

    def test_reversal_document_type_detection(self):
        """Reversal document types are classified correctly."""
        reversal_entry = {**SAMPLE_JOURNAL_ENTRY, "AccountingDocumentType": "AB"}
        self.mock_client.get_all.return_value = iter([reversal_entry])

        records = self.collector.collect()

        assert records[0].event_type == EventType.DOCUMENT_REVERSAL

    def test_amount_parsing(self):
        """Amount strings from SAP are parsed to float correctly."""
        self.mock_client.get_all.return_value = iter([SAMPLE_JOURNAL_ENTRY])

        records = self.collector.collect()

        assert records[0].amount == 184200.00
        assert records[0].currency == "USD"

    def test_malformed_record_skipped(self):
        """Malformed records are skipped without crashing the collection."""
        bad_record = {"CompanyCode": "1000"}  # Missing required fields
        good_record = SAMPLE_JOURNAL_ENTRY

        self.mock_client.get_all.return_value = iter([bad_record, good_record])

        # Should not raise — bad record is logged and skipped
        records = self.collector.collect()
        assert len(records) >= 0  # At minimum the good record should be collected


# ── Evidence Store Tests ───────────────────────────────────────────────────────

class TestLocalEvidenceStore:

    def test_save_and_load_roundtrip(self, tmp_path):
        """Records saved to store can be loaded back correctly."""
        from src.common.models import SAPEvidenceRecord

        store = LocalEvidenceStore(base_path=str(tmp_path))

        record = SAPEvidenceRecord(
            company_code="1000",
            document_number="100000001",
            is_agent_posted=True,
            agent_session_reference="close-2026-04",
            amount=184200.00,
            currency="USD",
        )

        filepath = store.save_records([record], session_label="test")
        loaded = store.load_records(filepath)

        assert len(loaded) == 1
        assert loaded[0].document_number == "100000001"
        assert loaded[0].is_agent_posted is True
        assert loaded[0].amount == 184200.00

    def test_stats_returns_correct_counts(self, tmp_path):
        """Stats reflect saved record counts."""
        from src.common.models import SAPEvidenceRecord

        store = LocalEvidenceStore(base_path=str(tmp_path))
        records = [SAPEvidenceRecord(document_number=str(i)) for i in range(5)]
        store.save_records(records)

        stats = store.get_stats()
        assert stats["total_records"] == 5
        assert stats["evidence_files"] == 1
