"""Adapter for the Bangladesh e-GP portal (eprocure.gov.bd).

Endpoint notes, established by inspection Sep 2026:

* The public tender list is NOT rendered into AllTenders.jsp. That page ships a
  jQuery handler that POSTs to /TenderDetailsServlet and splices the returned
  <tr> fragment into the table. We call that endpoint directly, so this adapter
  needs no browser (§5: Playwright only where JS is genuinely required).
* The app hands out a JSESSIONID on first contact and rejects requests without
  one, so fetch() primes a session before the first POST.
* Unknown paths return HTTP 200 with a "Session Expired" page. Status codes are
  therefore not a reliable success signal; we check for content markers.
* e-GP publishes no official estimated cost. Document price and tender security
  are the available money fields; security runs ~2-2.5% of the estimate and is
  the usable proxy for value-range matching.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from tenderradar.adapters.base import (
    FetchResult,
    PayloadKind,
    SourceAdapter,
    register_adapter,
)
from tenderradar.models import (
    OrganizationRef,
    ProcurementNature,
    TenderLot,
    TenderRecord,
    TenderStatus,
)

log = logging.getLogger(__name__)

DHAKA = ZoneInfo("Asia/Dhaka")

# "13-Sep-2026 13:00" is the portal's only date format, with and without time.
_DATE_FORMATS = ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%d/%m/%Y")

_NATURE_MAP = {
    "goods": ProcurementNature.GOODS,
    "works": ProcurementNature.WORKS,
    "services": ProcurementNature.SERVICES,
    "service": ProcurementNature.SERVICES,
}

_STATUS_MAP = {
    "live": TenderStatus.LIVE,
    "closed": TenderStatus.CLOSED,
    "cancelled": TenderStatus.CANCELLED,
    "canceled": TenderStatus.CANCELLED,
    "archived": TenderStatus.CLOSED,
    "awarded": TenderStatus.AWARDED,
}

# Marker proving we got a real result fragment rather than the session page.
_SESSION_EXPIRED = "Session Expired"


def parse_dhaka_datetime(value: str | None) -> datetime | None:
    """Parse a portal timestamp as Asia/Dhaka, return UTC (§9)."""
    if not value:
        return None
    text = " ".join(value.split())
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=DHAKA).astimezone(UTC)
    return None


def parse_money(value: str | None) -> Decimal | None:
    """Parse a BDT amount, tolerating thousands separators and stray text."""
    if not value:
        return None
    match = re.search(r"[\d,]+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        amount = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _method_code(value: str | None) -> str | None:
    """'Open Tendering Method (OTM)' -> 'OTM'; fall back to the full string."""
    if not value:
        return None
    match = re.search(r"\(([A-Za-z]{2,6})\)\s*$", value.strip())
    return match.group(1).upper() if match else value.strip() or None


def _lines(node: Node | None) -> list[str]:
    """Text of a node split on its <br> boundaries, trailing commas removed.

    Splitting on newlines rather than commas matters: organization names
    contain commas ("Ministry of Local Government, Rural Development and
    Co-operatives") but the portal separates fields with <br>.
    """
    if node is None:
        return []
    raw = node.text(separator="\n", strip=False)
    out = []
    for line in raw.split("\n"):
        cleaned = " ".join(line.split()).rstrip(",").strip()
        if cleaned:
            out.append(cleaned)
    return out


def _label_key(text: str) -> str:
    """Normalize a detail-page label into a stable dict key."""
    return " ".join(text.split()).rstrip(":").strip().lower()


@register_adapter
class EgpTenderAdapter(SourceAdapter):
    """e-GP tender search: the live/archived e-tender list and detail pages."""

    key = "egp_tender"
    name = "Bangladesh e-GP (e-Tenders)"
    base_url = "https://www.eprocure.gov.bd"
    expected_yield_per_run = 3700

    LIST_PAGE = "/resources/common/AllTenders.jsp?h=t"
    SERVLET = "/TenderDetailsServlet"
    DETAIL_PAGE = "/resources/common/ViewTender.jsp"

    # The servlet accepts a large page size; 100 keeps a full live sweep to
    # ~38 requests instead of ~380, which is gentler on the host than paging
    # at the UI default of 10.
    PAGE_SIZE = 100

    def _search_payload(self, page_no: int, view_type: str, size: int) -> dict[str, str]:
        """Exactly the fields the portal's own jQuery handler sends."""
        return {
            "funName": "AllTenders",
            "viewType": view_type,
            "departmentId": "",
            "office": "",
            "procNature": "",
            "procType": "",
            "procMethod": "",
            "tenderId": "",
            "refNo": "",
            "pubDtFrm": "",
            "pubDtTo": "",
            "closeDtFrm": "",
            "closeDtTo": "",
            "cpvCategory": "",
            "isFrame": "",
            "pageNo": str(page_no),
            "size": str(size),
            "h": "t",
        }

    async def _prime_session(self) -> None:
        """Obtain a JSESSIONID; the servlet rejects sessionless POSTs."""
        await self.client.get(self.LIST_PAGE)

    async def fetch(  # type: ignore[override]
        self,
        *,
        view_type: str = "Live",
        max_pages: int | None = None,
        page_size: int | None = None,
        detail_ids: list[str] | None = None,
        **_: object,
    ) -> AsyncIterator[FetchResult]:
        """Yield raw payloads: list pages, or detail pages for given ids.

        Passing detail_ids fetches only those detail pages. That is the normal
        path: a full sweep pulls the cheap list, and only tenders that are new
        or whose hash changed get a detail fetch.
        """
        await self._prime_session()

        if detail_ids is not None:
            for tender_id in detail_ids:
                async for result in self._fetch_detail(tender_id):
                    yield result
            return

        size = page_size or self.PAGE_SIZE
        page_no = 1
        total_pages: int | None = None

        while True:
            response = await self.client.post(
                self.SERVLET,
                data=self._search_payload(page_no, view_type, size),
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}{self.LIST_PAGE}",
                },
            )
            body = response.text

            if _SESSION_EXPIRED in body:
                # Session died mid-sweep. Re-prime once and retry this page.
                log.warning("e-GP session expired at page %d; re-priming", page_no)
                await self._prime_session()
                response = await self.client.post(
                    self.SERVLET,
                    data=self._search_payload(page_no, view_type, size),
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                body = response.text

            yield FetchResult(
                url=f"{self.base_url}{self.SERVLET}",
                content=response.content,
                kind=PayloadKind.LIST,
                content_type=response.headers.get("content-type", "text/html"),
                meta={"page_no": page_no, "size": size, "view_type": view_type},
            )

            if total_pages is None:
                total_pages = self._total_pages(body)
                log.info(
                    "e-GP %s sweep: %s pages at size %d", view_type, total_pages, size
                )

            if "noRecordFound" in body or total_pages is None or page_no >= total_pages:
                break
            if max_pages is not None and page_no >= max_pages:
                break
            page_no += 1

    async def _fetch_detail(self, tender_id: str) -> AsyncIterator[FetchResult]:
        response = await self.client.post(
            self.DETAIL_PAGE,
            data={"id": tender_id, "h": "t"},
            headers={"Referer": f"{self.base_url}{self.LIST_PAGE}"},
        )
        yield FetchResult(
            url=f"{self.base_url}{self.DETAIL_PAGE}?id={tender_id}",
            content=response.content,
            kind=PayloadKind.DETAIL,
            content_type=response.headers.get("content-type", "text/html"),
            meta={"tender_id": tender_id},
        )

    @staticmethod
    def _total_pages(body: str) -> int | None:
        match = re.search(r'id="totalPages"[^>]*value="(\d+)"', body)
        return int(match.group(1)) if match else None

    def parse(self, result: FetchResult) -> list[TenderRecord]:
        if result.kind is PayloadKind.LIST:
            return self._parse_list(result)
        if result.kind is PayloadKind.DETAIL:
            record = self._parse_detail(result)
            return [record] if record else []
        return []

    # ---------------------------------------------------------------- list

    def _parse_list(self, result: FetchResult) -> list[TenderRecord]:
        html = result.content.decode("utf-8", errors="replace")
        if _SESSION_EXPIRED in html:
            return []

        # The servlet returns a bare run of <tr> with no enclosing table. An
        # HTML5 parser discards rows outside a table, so wrap before parsing.
        if "<table" not in html[:200].lower():
            html = f"<table>{html}</table>"

        records: list[TenderRecord] = []
        for row in HTMLParser(html).css("tr"):
            cells = row.css("td")
            if len(cells) < 6:
                continue
            record = self._parse_list_row(cells, result)
            if record is not None:
                records.append(record)
        return records

    def _parse_list_row(
        self, cells: list[Node], result: FetchResult
    ) -> TenderRecord | None:
        # cell 1: tender id / invitation reference no / status, one per <br>
        ident = _lines(cells[1])
        if not ident or not ident[0].isdigit():
            return None
        external_ref = ident[0]
        reference_no = ident[1] if len(ident) > 1 else None
        status_text = ident[2] if len(ident) > 2 else "Live"

        # cell 2: nature, then package no and brief description
        brief = _lines(cells[2])
        nature = (
            _NATURE_MAP.get(brief[0].lower(), ProcurementNature.OTHER)
            if brief
            else ProcurementNature.OTHER
        )
        package_no = brief[1] if len(brief) > 1 else None
        description = " ".join(brief[2:]) if len(brief) > 2 else None

        # cell 3: ministry / division / agency / procuring entity
        organization = self._organization_from_parts(_lines(cells[3]))

        # cell 4: procurement type, procurement method
        proc = _lines(cells[4])
        procurement_type = proc[0] if proc else None
        procurement_method = _method_code(proc[1]) if len(proc) > 1 else None

        # cell 5: published, closing
        dates = _lines(cells[5])
        published_at = parse_dhaka_datetime(dates[0]) if dates else None
        closing_at = parse_dhaka_datetime(dates[1]) if len(dates) > 1 else None

        return TenderRecord(
            source_key=self.key,
            external_ref=external_ref,
            title=description or package_no,
            package_no=package_no,
            reference_no=reference_no,
            description=description,
            organization=organization,
            procurement_nature=nature,
            procurement_type=procurement_type,
            procurement_method=procurement_method,
            published_at=published_at,
            closing_at=closing_at,
            status=_STATUS_MAP.get(status_text.lower(), TenderStatus.LIVE),
            detail_url=f"{self.base_url}{self.DETAIL_PAGE}?id={external_ref}",
            raw_document_id=result.raw_document_id,
        )

    @staticmethod
    def _organization_from_parts(parts: list[str]) -> OrganizationRef:
        """Map the 1-4 hierarchy lines onto the ref, deepest last.

        Short hierarchies are right-aligned: a 2-line entry is an agency and a
        procuring entity, not a ministry and a division.
        """
        padded: list[str | None]
        if len(parts) < 4:
            padded = [None] * (4 - len(parts)) + list(parts)
        else:
            padded = list(parts[:4])
        return OrganizationRef(
            ministry=padded[0],
            division=padded[1],
            agency=padded[2],
            procuring_entity=padded[3],
        )

    # -------------------------------------------------------------- detail

    def _parse_detail(self, result: FetchResult) -> TenderRecord | None:
        html = result.content.decode("utf-8", errors="replace")
        if _SESSION_EXPIRED in html:
            return None

        tree = HTMLParser(html)
        fields = self._detail_fields(tree)
        if not fields:
            return None

        external_ref = self._first_present(fields, "tender/proposal id") or str(
            result.meta.get("tender_id", "")
        )
        if not external_ref:
            return None

        package_no, description = self._split_package(
            self._first_present(
                fields,
                "tender/proposal package no. and description",
                "brief description of works",
            )
        )

        categories = [
            c.strip()
            for c in (self._first_present(fields, "category") or "").replace(",", ";").split(";")
            if c.strip()
        ]

        nature = _NATURE_MAP.get(
            (self._first_present(fields, "procurement nature") or "").lower(), ProcurementNature.OTHER
        )
        status = _STATUS_MAP.get(
            (self._first_present(fields, "tender/proposal status") or "live").lower(), TenderStatus.LIVE
        )

        lots = self._parse_lots(tree)
        # Security is stated per lot; the tender-level proxy is their sum.
        security = sum(
            (lot.security_amount or Decimal(0) for lot in lots), Decimal(0)
        )

        return TenderRecord(
            source_key=self.key,
            external_ref=external_ref,
            title=description or package_no,
            package_no=package_no,
            reference_no=self._first_present(fields, "invitation reference no."),
            description=description,
            organization=OrganizationRef(
                ministry=self._first_present(fields, "ministry"),
                division=self._first_present(fields, "division"),
                agency=self._first_present(fields, "organization"),
                procuring_entity=self._first_present(fields, "procuring entity name"),
            ),
            district=self._first_present(fields, "procuring entity district"),
            upazila=lots[0].location if lots and lots[0].location else None,
            procurement_nature=nature,
            procurement_type=self._first_present(fields, "procurement type"),
            procurement_method=_method_code(self._first_present(fields, "procurement method")),
            tender_security=security or None,
            document_price=parse_money(
                self._first_present(
                    fields,
                    "tender/proposal document price (in bdt)",
                    "tender document price (in bdt)",
                )
            ),
            published_at=parse_dhaka_datetime(
                self._first_present(
                    fields, "scheduled tender/proposal publication date and time"
                )
            ),
            closing_at=parse_dhaka_datetime(
                self._first_present(
                    fields, "tender/proposal closing date and time"
                )
            ),
            opening_at=parse_dhaka_datetime(
                self._first_present(
                    fields, "tender/proposal opening date and time"
                )
            ),
            document_last_selling_at=parse_dhaka_datetime(
                self._first_present(
                fields,
                "tender/proposal document last selling / downloading date and time",
            )
            ),
            status=status,
            categories=categories,
            lots=lots,
            eligibility_text=self._first_present(fields, "eligibility of tenderer"),
            project_name=self._first_present(fields, "project name"),
            app_id=self._first_present(fields, "app id") or None,
            detail_url=result.url,
            raw_document_id=result.raw_document_id,
        )

    @staticmethod
    def _first_present(fields: dict[str, str], *keys: str) -> str | None:
        """Look up the first matching label, ignoring whitespace differences.

        Detail labels are split across <br> and cell padding, so the same field
        renders as both "Publication Date" and "PublicationDate" depending on
        how the row is laid out. Comparing whitespace-free keys sidesteps that.
        """
        squashed = {
            "".join(key.split()): value for key, value in fields.items()
        }
        for key in keys:
            value = fields.get(key) or squashed.get("".join(key.split()))
            if value:
                return value
        return None

    @staticmethod
    def _detail_fields(tree: HTMLParser) -> dict[str, str]:
        """Flatten the detail page's label/value cell pairs into a dict.

        Pair-walking rather than fixed indexing keeps this working when the
        portal adds or reorders rows.
        """
        fields: dict[str, str] = {}
        for row in tree.css("tr"):
            cells = row.css("td")
            index = 0
            while index + 1 < len(cells):
                label = cells[index].text(separator=" ", strip=True)
                if label.rstrip().endswith(":"):
                    key = _label_key(label)
                    value = cells[index + 1].text(separator="\n", strip=True)
                    value = "\n".join(
                        " ".join(line.split())
                        for line in value.split("\n")
                        if line.strip()
                    )
                    if key and key not in fields:
                        fields[key] = value
                    index += 2
                else:
                    index += 1
        return fields

    @staticmethod
    def _split_package(value: str | None) -> tuple[str | None, str | None]:
        """First line is the package number, the rest is the description."""
        if not value:
            return None, None
        parts = [p.strip() for p in value.split("\n") if p.strip()]
        if not parts:
            return None, None
        if len(parts) == 1:
            return None, parts[0]
        return parts[0], " ".join(parts[1:])

    @staticmethod
    def _parse_lots(tree: HTMLParser) -> list[TenderLot]:
        """Rows of the lot table: no, description, location, security, dates."""
        lots: list[TenderLot] = []
        for row in tree.css("tr"):
            cells = row.css("td")
            if len(cells) != 6:
                continue
            values = [c.text(separator=" ", strip=True) for c in cells]
            # A lot row starts with a bare lot number and carries an amount.
            if not values[0].isdigit():
                continue
            security = parse_money(values[3])
            if security is None:
                continue
            lots.append(
                TenderLot(
                    lot_no=values[0],
                    description=" ".join(values[1].split()) or None,
                    location=" ".join(values[2].split()) or None,
                    security_amount=security,
                    start_date=parse_dhaka_datetime(values[4]),
                    completion_date=parse_dhaka_datetime(values[5]),
                )
            )
        return lots
