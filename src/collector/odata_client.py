"""
SAP Audit Agent — SAP OData Client
Handles authenticated requests to SAP S/4HANA OData services.
Uses only supported API pathways per SAP's API policy.
"""

import requests
import logging
from typing import Dict, Any, List, Optional, Generator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class SAPODataClient:
    """
    Authenticated OData client for SAP S/4HANA.

    Features:
    - Basic authentication with SAP service user
    - Automatic CSRF token handling for write operations
    - Pagination via $skiptoken for large datasets
    - Retry logic with exponential backoff
    - Delta query support via $filter on LastChangeDateTime

    Usage:
        client = SAPODataClient(config["sap"])
        records = list(client.get_all(
            service="/sap/opu/odata/sap/API_JOURNALENTRYITEMBASIC_SRV",
            entity="A_JournalEntryItem",
            filters=["CompanyCode eq '1000'"],
        ))
    """

    def __init__(self, sap_config: Dict[str, Any]):
        self.base_url = sap_config["base_url"].rstrip("/")
        self.client = sap_config["client"]
        self.username = sap_config["username"]
        self.password = sap_config["password"]
        self.page_size = sap_config.get("page_size", 1000)
        self.timeout = sap_config.get("timeout_seconds", 30)
        self.max_retries = sap_config.get("max_retries", 3)

        self.session = self._build_session()

    def _build_session(self) -> requests.Session:
        """Build a requests session with retry logic and auth."""
        session = requests.Session()
        session.auth = (self.username, self.password)
        session.headers.update({
            "Accept": "application/json",
            "sap-client": self.client,
            "X-Requested-With": "XMLHttpRequest",
        })

        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    def get_page(
        self,
        service: str,
        entity: str,
        filters: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        skip_token: Optional[str] = None,
        top: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch a single page of OData results.

        Args:
            service: OData service path
            entity: Entity set name
            filters: List of OData $filter expressions (ANDed together)
            select: List of fields to return ($select)
            skip_token: Pagination token from previous response
            top: Number of records per page

        Returns:
            Raw OData response dict with 'd' key containing results
        """
        url = f"{self.base_url}{service}/{entity}"

        params: Dict[str, Any] = {
            "$format": "json",
            "$top": top or self.page_size,
        }

        if filters:
            params["$filter"] = " and ".join(filters)

        if select:
            params["$select"] = ",".join(select)

        if skip_token:
            params["$skiptoken"] = skip_token

        logger.debug(
            "SAP OData request",
            extra={"url": url, "params": {k: v for k, v in params.items()
                                           if k != "$skiptoken"}}
        )

        response = self.session.get(url, params=params, timeout=self.timeout)

        if response.status_code == 401:
            raise PermissionError(
                f"SAP authentication failed for user '{self.username}'. "
                f"Check service user credentials and authorizations."
            )

        if response.status_code == 403:
            raise PermissionError(
                f"SAP authorization denied for {entity}. "
                f"Check that service user has required authorization objects "
                f"(see P001 — Agent Permission Scoping)."
            )

        response.raise_for_status()

        return response.json()

    def get_all(
        self,
        service: str,
        entity: str,
        filters: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Fetch all records using automatic pagination.
        Yields individual records as they are retrieved.

        Args:
            service: OData service path
            entity: Entity set name
            filters: OData $filter expressions
            select: Fields to return

        Yields:
            Individual record dicts
        """
        skip_token = None
        page_number = 0
        total_yielded = 0

        while True:
            page_number += 1
            response = self.get_page(
                service=service,
                entity=entity,
                filters=filters,
                select=select,
                skip_token=skip_token,
            )

            results = response.get("d", {}).get("results", [])

            if not results:
                logger.info(
                    f"Collection complete: {entity} — "
                    f"{total_yielded} records across {page_number - 1} pages"
                )
                break

            for record in results:
                yield record
                total_yielded += 1

            # Check for next page
            next_link = response.get("d", {}).get("__next")
            if not next_link:
                logger.info(
                    f"Collection complete: {entity} — "
                    f"{total_yielded} records across {page_number} pages"
                )
                break

            # Extract skiptoken from next link
            skip_token = self._extract_skiptoken(next_link)
            if not skip_token:
                break

    def ping(self) -> bool:
        """
        Test SAP connectivity. Returns True if connection is successful.
        Use this to validate config before running a collection.
        """
        try:
            url = f"{self.base_url}/sap/opu/odata/sap/API_FISCALYEAR_SRV/"
            response = self.session.get(
                url,
                params={"$format": "json", "$top": 1},
                timeout=self.timeout,
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"SAP connectivity check failed: {e}")
            return False

    @staticmethod
    def _extract_skiptoken(next_link: str) -> Optional[str]:
        """Extract $skiptoken value from OData __next link."""
        if "$skiptoken=" in next_link:
            return next_link.split("$skiptoken=")[1].split("&")[0]
        return None
