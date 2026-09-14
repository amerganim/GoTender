"""Adapter for e-GP **offline** tender notices.

Offline tenders are notices published through e-GP but bid on paper rather than
electronically. They are a separate feed with a separate servlet, and they are
why Gate 0's coverage target is unreachable from the e-tender adapter alone:
the live e-tender pool is ~3,740 against §2's ~8,500 figure.

Same shape as the e-tender feed -- a JSP page whose jQuery POSTs to a servlet
and splices a bare <tr> fragment into a table -- so the adapter framework
carries most of it. Four things genuinely differ, and each is a trap:

1. **The third organization line is a person.** Rows read
   [ministry, department, "MD NAZMUL HUDA"]. Right-aligning the hierarchy the
   way the e-tender adapter does would file a human being as the procuring
   entity.
2. **Closing dates carry no time.** "14-Sep-2026" and nothing else. Parsed
   naively that becomes 00:00, i.e. the *start* of the closing day, so a
   tender would be treated as closed a day early and silently withheld from
   everyone it matched.
3. **The feed includes already-closed tenders.** The e-tender search is
   filtered to Live; this one returns everything, so status has to be derived
   rather than assumed.
4. **Detail links are opaque.** ViewTenderWithoutPQ.jsp?p=<hash> rather than
   an id, so the hash is the only way back to the notice.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta

from selectolax.parser import HTMLParser, Node

from tenderradar.adapters.base import (
    FetchResult,
    PayloadKind,
    SourceAdapter,
    register_adapter,
)
from tenderradar.adapters.egp import (
    DHAKA,
    _NATURE_MAP,
    _SESSION_EXPIRED,
    _lines,
    _method_code,
    is_portal_test_record,
    parse_dhaka_datetime,
)
from tenderradar.models import (
    OrganizationRef,
    ProcurementNature,
    TenderRecord,
    TenderStatus,
)

log = logging.getLogger(__name__)

_DETAIL_RE = re.compile(r"ViewTenderWithoutPQ\.jsp\?p=([A-Za-z0-9]+)")

# How long a notice with no stated closing date is still treated as possibly
# open. Generous on purpose: tender windows rarely exceed 90 days and Layer 1
# will not look past 90 either, so 180 cannot hide anything a user could still
# bid on, while comfortably catching decade-old archive rows.
STALE_WITHOUT_CLOSING = timedelta(days=180)


def parse_closing_date(value: str | None) -> datetime | None:
    """Parse a date-only closing value as the END of that day in Dhaka.

    The feed gives "14-Sep-2026" with no time. Treating that as 00:00 would
    mark the tender closed before its closing day even began, and Layer 1
    filters on closing_at > now(), so every affected tender would vanish from
    matching a day early. Nobody would ever report it -- they simply would not
    hear about tenders they could have bid on.

    Overstating by a few hours is the safe direction: the worst case is a
    tender shown slightly too long, against silently withholding a live one.
    """
    parsed = parse_dhaka_datetime(value)
    if parsed is None:
        return None
    local = parsed.astimezone(DHAKA)
    if local.timetz().replace(tzinfo=None) != time(0, 0):
        return parsed  # a real time was supplied; leave it alone
    end_of_day = datetime.combine(local.date(), time(23, 59), tzinfo=DHAKA)
    return end_of_day.astimezone(UTC)


@register_adapter
class EgpOfflineTenderAdapter(SourceAdapter):
    """e-GP offline (paper-bid) tender notices."""

    key = "egp_offline"
    name = "Bangladesh e-GP (Offline Tenders)"
    base_url = "https://www.eprocure.gov.bd"
    expected_yield_per_run = 680

    LIST_PAGE = "/resources/common/SearchTenderOffline.jsp"
    SERVLET = "/TenderDashboardOfflineServlet"

    PAGE_SIZE = 100

    def _search_payload(self, page_no: int, size: int) -> dict[str, str]:
        """Exactly the fields the portal's own handler sends."""
        return {
            "action": "BindGridHomeNew",
            "statusTab": "",
            "tenderId": "",
            "refNo": "",
            "procNature": "",
            "procType": "",
            "procMethod": "",
            "pubDtFrm": "",
            "pubDtTo": "",
            "pageNo": str(page_no),
            "size": str(size),
        }

    async def _prime_session(self) -> None:
        await self.client.get(self.LIST_PAGE)

    async def fetch(  # type: ignore[override]
        self,
        *,
        max_pages: int | None = None,
        page_size: int | None = None,
        **_: object,
    ) -> AsyncIterator[FetchResult]:
        await self._prime_session()

        size = page_size or self.PAGE_SIZE
        page_no = 1
        total_pages: int | None = None

        while True:
            response = await self.client.post(
                self.SERVLET,
                data=self._search_payload(page_no, size),
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}{self.LIST_PAGE}",
                },
            )
            body = response.text

            if _SESSION_EXPIRED in body:
                log.warning("offline session expired at page %d; re-priming", page_no)
                await self._prime_session()
                response = await self.client.post(
                    self.SERVLET,
                    data=self._search_payload(page_no, size),
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                body = response.text

            yield FetchResult(
                url=f"{self.base_url}{self.SERVLET}",
                content=response.content,
                kind=PayloadKind.LIST,
                content_type=response.headers.get("content-type", "text/html"),
                meta={"page_no": page_no, "size": size},
            )

            if total_pages is None:
                total_pages = self._total_pages(body)
                log.info("e-GP offline sweep: %s pages at size %d", total_pages, size)

            if "noRecordFound" in body or total_pages is None or page_no >= total_pages:
                break
            if max_pages is not None and page_no >= max_pages:
                break
            page_no += 1

    @staticmethod
    def _total_pages(body: str) -> int | None:
        match = re.search(r'id="totalPages"[^>]*value="(\d+)"', body)
        return int(match.group(1)) if match else None

    def parse(self, result: FetchResult) -> list[TenderRecord]:
        if result.kind is not PayloadKind.LIST:
            return []

        html = result.content.decode("utf-8", errors="replace")
        if _SESSION_EXPIRED in html:
            return []
        # Bare <tr> with no table; an HTML5 parser discards those.
        if "<table" not in html[:200].lower():
            html = f"<table>{html}</table>"

        records = []
        for row in HTMLParser(html).css("tr"):
            cells = row.css("td")
            if len(cells) < 6:
                continue
            record = self._parse_row(cells, result)
            if record is not None:
                records.append(record)
        return records

    def _parse_row(
        self, cells: list[Node], result: FetchResult
    ) -> TenderRecord | None:
        ident = _lines(cells[1])
        if not ident or not ident[0].isdigit():
            return None
        external_ref = ident[0]
        reference_no = ident[1] if len(ident) > 1 else None

        brief = _lines(cells[2])
        nature = (
            _NATURE_MAP.get(brief[0].lower(), ProcurementNature.OTHER)
            if brief
            else ProcurementNature.OTHER
        )
        description = " ".join(brief[1:]) if len(brief) > 1 else None

        if is_portal_test_record(description):
            log.info("skipping e-GP demonstration record %s (offline)", external_ref)
            return None

        organization = self._organization_from_parts(_lines(cells[3]))

        proc = _lines(cells[4])
        procurement_type = proc[0] if proc else None
        procurement_method = _method_code(proc[1]) if len(proc) > 1 else None

        dates = _lines(cells[5])
        published_at = parse_dhaka_datetime(dates[0]) if dates else None
        closing_at = parse_closing_date(dates[1]) if len(dates) > 1 else None

        # This feed returns closed notices alongside open ones, so status is
        # derived rather than assumed Live.
        status = self._derive_status(published_at, closing_at)

        detail_url = None
        link = re.search(_DETAIL_RE, cells[2].html or "")
        if link:
            detail_url = (
                f"{self.base_url}/resources/common/"
                f"ViewTenderWithoutPQ.jsp?p={link.group(1)}"
            )

        return TenderRecord(
            source_key=self.key,
            external_ref=external_ref,
            title=description,
            reference_no=reference_no,
            description=description,
            organization=organization,
            procurement_nature=nature,
            procurement_type=procurement_type,
            procurement_method=procurement_method,
            published_at=published_at,
            closing_at=closing_at,
            status=status,
            detail_url=detail_url,
            raw_document_id=result.raw_document_id,
        )

    @staticmethod
    def _derive_status(
        published_at: datetime | None, closing_at: datetime | None
    ) -> TenderStatus:
        """Decide live vs closed without ever assuming live by default.

        A missing closing date used to fall through to Live, which meant a
        notice with no stated deadline stayed live forever. On this feed that
        put 35 notices published between 2004 and 2014 on the site as current
        tenders -- the oldest twelve years stale.

        Unknown is not the same as open. Where there is no closing date, age
        decides: a notice published within the staleness window might genuinely
        still be open and is left Live so a browsing contractor can see it and
        judge; an older one certainly is not.

        Note that a Live tender with no closing date still will not be alerted
        on, because Layer 1 filters on the closing window. That is deliberate:
        we will show a notice whose deadline we do not know, but we will not
        promise someone a deadline we cannot state.
        """
        if closing_at is not None:
            return (
                TenderStatus.CLOSED
                if closing_at <= datetime.now(UTC)
                else TenderStatus.LIVE
            )
        if published_at is None:
            # No closing date and no publication date is not evidence of a live
            # tender; it is evidence of a row we cannot date at all.
            return TenderStatus.CLOSED
        age = datetime.now(UTC) - published_at
        return (
            TenderStatus.CLOSED
            if age > STALE_WITHOUT_CLOSING
            else TenderStatus.LIVE
        )

    @staticmethod
    def _organization_from_parts(parts: list[str]) -> OrganizationRef:
        """Map [ministry, department, contact person] onto the hierarchy.

        The third line is the named official inviting the tender, not an
        organization. The e-tender adapter right-aligns its four levels, which
        here would file a person as the procuring entity and then show their
        name wherever the buyer belongs. It is dropped instead: the record has
        nowhere honest to keep it, and a wrong organization is worse than an
        absent contact name.
        """
        cleaned = [p for p in parts if p]
        ministry = cleaned[0] if cleaned else None
        agency = cleaned[1] if len(cleaned) > 1 else None
        # deepest falls through to agency, which is the name a bidder
        # recognizes as the buyer.
        return OrganizationRef(ministry=ministry, agency=agency)
