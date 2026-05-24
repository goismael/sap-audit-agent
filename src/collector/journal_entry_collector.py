"""
SAP Audit Agent — Journal Entry Collector
Collects journal entry line items from SAP S/4HANA via OData.
Identifies agent-posted documents using the service user registry.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Set

from ..common.models import SAPEvidenceRecord, EventType, CollectionState
from .odata_client import SAPODataClient

logger = logging.getLogger(__name__)

# Fields to retrieve from SAP — only what we need for audit evidence
JOURNAL_ENTRY_SELECT_FIELDS = [
    "CompanyCode",
    "FiscalYear",
    "AccountingDocument",
    "FiscalPeriod",
    "PostingDate",
    "AccountingDocumentType",
    "DocumentReferenceID",
    "DocumentHeaderText",
    "CreatedByUser",
    "CreationDate",
    "CreationTime",
    "GLAccount",
    "AmountInCompanyCodeCurrency",
    "CompanyCodeCurrency",
    "CostCenter",
    "ProfitCenter",
    "LastChangeDate",
]

# Document types that indicate reversals
REVERSAL_DOCUMENT_TYPES = {"AB", "RV", "RE", "RF", "RG"}


class JournalEntryCollector:
    """
    Collects journal entry line items from SAP S/4HANA.

    Identifies which postings were made by AI agents by matching
    the CreatedByUser field against the known agent service user
    registry defined in P001.

    Delta collection: only retrieves records changed since last run,
    using LastChangeDate as the filter field.
    """

    SERVICE = "/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV"
    ENTITY = "A_JournalEntryItem"

    def __init__(
        self,
        client: SAPODataClient,
        agent_service_users: Set[str],
        company_codes: List[str],
        fiscal_year: str,
    ):
        self.client = client
        self.agent_service_users = {u.upper() for u in agent_service_users}
        self.company_codes = company_codes
        self.fiscal_year = fiscal_year

    def collect(
        self,
        state: Optional[CollectionState] = None,
        lookback_days: int = 30,
    ) -> List[SAPEvidenceRecord]:
        """
        Collect journal entry records for all configured company codes.

        Args:
            state: Previous collection state for delta queries.
                   If None, collects records from the last lookback_days.
            lookback_days: How far back to look when no state exists.

        Returns:
            List of SAPEvidenceRecord instances ready for correlation.
        """
        records = []

        # Determine the date filter
        if state and state.last_document_date:
            since_date = state.last_document_date
            logger.info(f"Delta collection from {since_date}")
        else:
            since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            since_date = since.strftime("%Y-%m-%d")
            logger.info(f"Initial collection from {since_date} ({lookback_days} days back)")

        for company_code in self.company_codes:
            logger.info(f"Collecting journal entries: company code {company_code}")
            cc_records = self._collect_company_code(company_code, since_date)
            records.extend(cc_records)
            logger.info(
                f"Collected {len(cc_records)} journal entries "
                f"for company code {company_code}"
            )

        logger.info(f"Total journal entries collected: {len(records)}")
        return records

    def _collect_company_code(
        self,
        company_code: str,
        since_date: str,
    ) -> List[SAPEvidenceRecord]:
        """Collect journal entries for a single company code."""
        filters = [
            f"CompanyCode eq '{company_code}'",
            f"FiscalYear eq '{self.fiscal_year}'",
            f"LastChangeDate ge datetime'{since_date}T00:00:00'",
        ]

        records = []
        for raw in self.client.get_all(
            service=self.SERVICE,
            entity=self.ENTITY,
            filters=filters,
            select=JOURNAL_ENTRY_SELECT_FIELDS,
        ):
            try:
                record = self._map_to_evidence_record(raw, company_code)
                records.append(record)
            except Exception as e:
                logger.error(
                    f"Failed to map journal entry record: {e}",
                    extra={"raw": raw}
                )

        return records

    def _map_to_evidence_record(
        self,
        raw: Dict[str, Any],
        company_code: str,
    ) -> SAPEvidenceRecord:
        """Map a raw OData response to a SAPEvidenceRecord."""
        posted_by = raw.get("CreatedByUser", "").upper()
        is_agent_posted = posted_by in self.agent_service_users

        # Determine event type
        doc_type = raw.get("AccountingDocumentType", "")
        if doc_type in REVERSAL_DOCUMENT_TYPES:
            event_type = EventType.DOCUMENT_REVERSAL
        else:
            event_type = EventType.JOURNAL_ENTRY_POSTING

        # Extract session reference from document reference field
        # Agent postings store the session_id in DocumentReferenceID
        reference = raw.get("DocumentReferenceID", "")
        session_ref = reference if (is_agent_posted and reference) else None

        # Parse amount — SAP returns as string in OData v2
        amount_str = raw.get("AmountInCompanyCodeCurrency", "0")
        try:
            amount = float(amount_str)
        except (ValueError, TypeError):
            amount = 0.0

        return SAPEvidenceRecord(
            source_service=self.SERVICE,
            event_type=event_type,
            company_code=company_code,
            fiscal_year=raw.get("FiscalYear", ""),
            period=raw.get("FiscalPeriod", ""),
            document_number=raw.get("AccountingDocument", ""),
            posting_date=raw.get("PostingDate", ""),
            posting_time=raw.get("CreationTime", ""),
            document_type=doc_type,
            posted_by_user=posted_by,
            amount=amount,
            currency=raw.get("CompanyCodeCurrency", ""),
            gl_account=raw.get("GLAccount", ""),
            cost_center=raw.get("CostCenter") or None,
            profit_center=raw.get("ProfitCenter") or None,
            reference=reference,
            document_header_text=raw.get("DocumentHeaderText", ""),
            is_agent_posted=is_agent_posted,
            agent_session_reference=session_ref,
            raw_payload=raw,
        )

    def get_collection_state(
        self,
        records: List[SAPEvidenceRecord],
        company_code: str,
    ) -> CollectionState:
        """
        Build a CollectionState from completed collection results.
        Used to persist state for the next delta run.
        """
        dates = [
            r.posting_date for r in records
            if r.company_code == company_code and r.posting_date
        ]

        return CollectionState(
            last_run_at=datetime.now(timezone.utc).isoformat(),
            company_code=company_code,
            service=self.SERVICE,
            last_document_date=max(dates) if dates else None,
            documents_collected=len([
                r for r in records if r.company_code == company_code
            ]),
        )
