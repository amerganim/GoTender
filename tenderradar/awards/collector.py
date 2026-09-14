"""Contract award collection (§4 Phase 5, started early).

Awards are a different entity from tenders, so this does not pretend to be a
SourceAdapter: the adapter contract returns TenderRecords and the crawl runner
upserts into `tenders`, and bending either to carry awards would make both
harder to read. It reuses the parts that are genuinely shared -- the polite
HTTP client and the raw archive -- and keeps its own parse and store path.

Why collect now, when the FEATURES are gated behind Gate 4: awards cannot be
backfilled from the future. The portal holds roughly 877,000 historical awards
and adds more daily; every month not collecting is a month of history
permanently lost, and that archive is precisely what §4 says makes this hard to
copy. Storing rows is not shipping a product.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from psycopg import AsyncConnection
from selectolax.parser import HTMLParser, Node

from tenderradar.adapters.base import FetchResult, PayloadKind
from tenderradar.adapters.egp import _SESSION_EXPIRED, _lines, parse_dhaka_datetime
from tenderradar.crawl.http import PoliteClient
from tenderradar.models import normalize_text

log = logging.getLogger(__name__)

SOURCE_KEY = "egp_awards"
SOURCE_NAME = "Bangladesh e-GP (Contract Awards)"
BASE_URL = "https://www.eprocure.gov.bd"
LIST_PAGE = "/resources/common/SearchNOA.jsp"
SERVLET = "/SearchNoaServlet"

PAGE_SIZE = 100

_LINK_RE = re.compile(r"pkgLotId=(\d+)&(?:amp;)?tenderid=(\d+)")
_DATE_RE = re.compile(r"Date:\s*(\d{2}/\d{2}/\d{4})")


@dataclass(slots=True)
class AwardRecord:
    winner: str
    tender_external_ref: str | None = None
    pkg_lot_id: str | None = None
    reference_no: str | None = None
    title: str | None = None
    ministry: str | None = None
    procuring_entity: str | None = None
    procurement_method: str | None = None
    district_name: str | None = None
    value_crore: Decimal | None = None
    advertised_at: datetime | None = None
    contract_signed_at: datetime | None = None
    detail_url: str | None = None


@dataclass(slots=True)
class AwardReport:
    pages: int = 0
    found: int = 0
    stored: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"awards: {self.found} found, {self.stored} new, {self.pages} pages"
        if self.errors:
            text += f", {len(self.errors)} errors"
        return text


def parse_value_crore(text: str | None) -> Decimal | None:
    """Parse the published figure without converting it.

    The column is labelled "Value (Cr. BDT) /(Other Currency)", so the unit is
    crore BDT for most rows but not all. Multiplying by 10,000,000 here would
    bake a currency assumption into every row and quietly corrupt any analysis
    built on it later.
    """
    if not text:
        return None
    match = re.search(r"-?[\d,]+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        value = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def parse_awards(result: FetchResult) -> list[AwardRecord]:
    """Pure parse of one result page."""
    html = result.content.decode("utf-8", errors="replace")
    if _SESSION_EXPIRED in html:
        return []
    if "<table" not in html[:200].lower():
        html = f"<table>{html}</table>"

    records = []
    for row in HTMLParser(html).css("tr"):
        cells = row.css("td")
        if len(cells) < 8:
            continue
        record = _parse_row(cells)
        if record is not None:
            records.append(record)
    return records


def _parse_row(cells: list[Node]) -> AwardRecord | None:
    winner = normalize_text(cells[6].text(separator=" ", strip=True))
    if not winner:
        # A row with no winner is not an award; skip rather than storing a
        # blank in the column this table exists for.
        return None

    ministry = normalize_text(cells[1].text(separator=" ", strip=True))

    # Cell 2 packs id, reference, title and the advertisement date together.
    tender_ref = pkg_lot_id = None
    detail_url = None
    link = cells[2].css_first("a")
    if link is not None:
        href = link.attributes.get("href", "") or ""
        match = _LINK_RE.search(href)
        if match:
            pkg_lot_id, tender_ref = match.group(1), match.group(2)
            detail_url = f"{BASE_URL}/resources/common/ViewAwardedContracts.jsp?pkgLotId={pkg_lot_id}&tenderid={tender_ref}"

    ident = normalize_text(link.text(strip=True)) if link is not None else None
    reference_no = None
    if ident and "," in ident:
        _, _, reference_no = ident.partition(",")
        reference_no = normalize_text(reference_no)

    cell_text = cells[2].text(separator="\n", strip=True)
    title = None
    for line in cell_text.split("\n"):
        cleaned = normalize_text(line)
        if cleaned and cleaned != ident and not cleaned.startswith("Date:"):
            title = cleaned
            break

    advertised_at = None
    date_match = _DATE_RE.search(cell_text)
    if date_match:
        advertised_at = parse_dhaka_datetime(date_match.group(1))

    entity_lines = _lines(cells[3])
    procuring_entity = entity_lines[0] if entity_lines else None
    procurement_method = entity_lines[1] if len(entity_lines) > 1 else None

    return AwardRecord(
        winner=winner,
        tender_external_ref=tender_ref,
        pkg_lot_id=pkg_lot_id,
        reference_no=reference_no,
        title=title,
        ministry=ministry,
        procuring_entity=procuring_entity,
        procurement_method=procurement_method,
        district_name=normalize_text(cells[4].text(strip=True)),
        value_crore=parse_value_crore(cells[7].text(strip=True)),
        advertised_at=advertised_at,
        contract_signed_at=parse_dhaka_datetime(cells[5].text(strip=True)),
        detail_url=detail_url,
    )


async def fetch_pages(
    client: PoliteClient, *, max_pages: int, page_size: int = PAGE_SIZE
) -> AsyncIterator[FetchResult]:
    """Yield award result pages, newest first.

    The listing is ordered by signing date descending, so a daily incremental
    run only needs the first few pages; the deep archive is a separate,
    one-time backfill.
    """
    await client.get(LIST_PAGE)

    for page_no in range(1, max_pages + 1):
        response = await client.post(
            SERVLET,
            data={"keyword": "", "pageNo": str(page_no), "size": str(page_size)},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}{LIST_PAGE}",
            },
        )
        body = response.text
        yield FetchResult(
            url=f"{BASE_URL}{SERVLET}",
            content=response.content,
            kind=PayloadKind.LIST,
            content_type=response.headers.get("content-type", "text/html"),
            meta={"page_no": page_no, "size": page_size},
        )
        if "noRecordFound" in body:
            break


async def store_awards(
    conn: AsyncConnection,
    source_id: int,
    records: list[AwardRecord],
    raw_document_id: int | None = None,
) -> int:
    """Insert awards, ignoring ones already held.

    An award is immutable once signed, so a conflict means we already have it
    and there is nothing to update. That also makes an incremental run cheap:
    re-reading yesterday's page costs inserts that do nothing.
    """
    stored = 0
    for record in records:
        cur = await conn.execute(
            """
            INSERT INTO contract_awards
                (source_id, tender_external_ref, pkg_lot_id, reference_no, title,
                 ministry, procuring_entity, procurement_method, district_name,
                 winner, value_crore, advertised_at, contract_signed_at,
                 detail_url, raw_document_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, tender_external_ref, pkg_lot_id, winner)
                DO NOTHING
            RETURNING id
            """,
            (
                source_id, record.tender_external_ref, record.pkg_lot_id,
                record.reference_no, record.title, record.ministry,
                record.procuring_entity, record.procurement_method,
                record.district_name, record.winner, record.value_crore,
                record.advertised_at, record.contract_signed_at,
                record.detail_url, raw_document_id,
            ),
        )
        if await cur.fetchone() is not None:
            stored += 1
    return stored


async def ensure_source(conn: AsyncConnection) -> int:
    """Awards are their own source row, so crawl history stays separate."""
    cur = await conn.execute(
        """
        INSERT INTO sources (name, base_url, adapter_key, expected_yield_per_run)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (adapter_key) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (SOURCE_NAME, BASE_URL, SOURCE_KEY, PAGE_SIZE),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


async def collect(
    conn: AsyncConnection, *, max_pages: int = 5, page_size: int = PAGE_SIZE
) -> AwardReport:
    """Fetch, archive, parse and store award pages.

    Archive before parse, exactly as the tender crawler does (§8.4): the whole
    point of collecting early is that a better parser can be replayed over the
    archive later without re-crawling 877,000 rows.
    """
    from tenderradar.crawl.archive import RawArchive
    from tenderradar.db import repo

    report = AwardReport()
    source_id = await ensure_source(conn)
    await conn.commit()

    archive = RawArchive()
    client = PoliteClient(base_url=BASE_URL)
    try:
        async for result in fetch_pages(
            client, max_pages=max_pages, page_size=page_size
        ):
            report.pages += 1
            archived = archive.store(SOURCE_KEY, result)
            raw_id = await repo.record_raw(conn, source_id, None, result, archived)
            await conn.commit()

            try:
                records = parse_awards(result)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"page {report.pages}: {exc}")
                log.exception("award parse failed on page %d", report.pages)
                continue

            report.found += len(records)
            report.stored += await store_awards(conn, source_id, records, raw_id)
            await conn.commit()
    finally:
        await client.aclose()

    return report
