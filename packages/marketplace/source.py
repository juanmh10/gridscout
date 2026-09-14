import os
import re
import json
import logging
from typing import Protocol, List, Dict, Any, Optional
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright, Page, BrowserContext

from packages.marketplace.errors import MarketplaceOperationError, OlxAccessError

logger = logging.getLogger(__name__)

class SearchQuery(BaseModel):
    query: str
    category: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    olx_pay_only: bool = False
    delivery_only: bool = False
    require_price: bool = False
    limit: int = 20
    sort: str = "recent"
    page: int = Field(default=1, ge=1)

class SourceListingSummary(BaseModel):
    external_id: str
    title: str
    price: Optional[float] = None
    price_origin: str = "dom"
    price_raw: str = ""
    location: str = ""
    location_origin: str = "dom"
    condition: str = ""
    seller: str = ""
    url: str
    marketplace_item_id: Optional[str] = None
    field_coverage: Dict[str, bool] = Field(default_factory=dict)
    parser_version: str = "olx-dom-2026-08-24"

class SourceListingDetail(BaseModel):
    external_id: str
    title: str
    description: str
    price: float
    location_state: str = ""
    location_city: str = ""
    seller_name: str = ""
    seller_rating: float = 0.0
    condition: str = ""
    attributes: Dict[str, Any]
    source_url: str
    marketplace_item_id: Optional[str] = None
    delivery_text: str = ""
    seller_verification: str = "UNKNOWN"
    seller_evidence: List[str] = Field(default_factory=list)
    photos: List[Dict[str, Any]] = Field(default_factory=list)
    external_navigation_count: int = 0
    parser_version: str = "olx-dom-2026-08-24"

class MarketplaceSource(Protocol):
    async def search(self, query: SearchQuery) -> List[SourceListingSummary]:
        ...

    async def fetch_listing(self, external_id: str) -> SourceListingDetail:
        ...

    async def refresh_listing(self, external_id: str) -> SourceListingDetail:
        ...

class FixtureMarketplaceSource:
    """Default operational source in MVP mode reading deterministic synthetic fixture data."""

    def __init__(self, fixtures_dir: Optional[str] = None):
        self.fixtures_dir = fixtures_dir or os.getenv("FIXTURES_DIR", "fixtures/marketplace")

    async def search(self, query: SearchQuery) -> List[SourceListingSummary]:
        results: List[SourceListingSummary] = []
        json_path = os.path.join(self.fixtures_dir, "json", "listings.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    title = item.get("title", "")
                    if query.query.lower() in title.lower():
                        price = float(item.get("price", 0.0))
                        if (query.require_price or (query.min_price is not None and query.min_price > 0)) and price <= 0:
                            continue
                        if query.min_price and price < query.min_price:
                            continue
                        if query.max_price and price > query.max_price:
                            continue
                        results.append(SourceListingSummary(
                            external_id=str(item.get("external_id", "")),
                            title=title,
                            price=price,
                            location=f"{item.get('location_city', 'SP')}, {item.get('location_state', 'SP')}",
                            condition=item.get("condition", "good"),
                            seller=item.get("seller_name", "Vendedor"),
                            url=item.get("source_url", "")
                        ))
                start = (query.page - 1) * query.limit
                return results[start:start + query.limit]
        else:
            # The HTML fixture is the canonical small browser fixture in this
            # repository; keep the JSON adapter useful when only that asset is
            # present.
            html_source = PlaywrightFixtureMarketplaceSource(
                os.path.join(self.fixtures_dir, "html")
            )
            results = await html_source.search(query)
        return results

    async def fetch_listing(self, external_id: str) -> SourceListingDetail:
        json_path = os.path.join(self.fixtures_dir, "json", "listings.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    if str(item.get("external_id")) == str(external_id):
                        return SourceListingDetail(
                            external_id=str(item.get("external_id")),
                            title=item.get("title", ""),
                            description=item.get("description", ""),
                            price=float(item.get("price", 0.0)),
                            location_state=item.get("location_state", "SP"),
                            location_city=item.get("location_city", "São Paulo"),
                            seller_name=item.get("seller_name", "Vendedor"),
                            seller_rating=float(item.get("seller_rating", 5.0)),
                            condition=item.get("condition", "good"),
                            attributes=item.get("attributes", {}),
                            source_url=item.get("source_url", ""),
                            delivery_text=item.get("delivery_text", ""),
                            seller_verification=item.get("seller_verification", "UNKNOWN"),
                            seller_evidence=item.get("seller_evidence", []),
                            photos=item.get("photos", []),
                        )
        else:
            html_source = PlaywrightFixtureMarketplaceSource(
                os.path.join(self.fixtures_dir, "html")
            )
            return await html_source.fetch_listing(external_id)
        raise ValueError(f"Fixture listing '{external_id}' not found.")

    async def refresh_listing(self, external_id: str) -> SourceListingDetail:
        return await self.fetch_listing(external_id)

class PlaywrightFixtureMarketplaceSource:
    """Extracts marketplace listing data from local HTML fixture pages using Playwright."""

    def __init__(self, base_url_or_file_path: str):
        self.base_path = base_url_or_file_path.rstrip("/")

    def extract_search_page(self, page: Page) -> List[SourceListingSummary]:
        listings = []
        cards = page.query_selector_all(".listing-card")
        for card in cards:
            ext_id = card.get_attribute("data-id") or ""
            title_elem = card.query_selector(".title a")
            title = title_elem.inner_text() if title_elem else ""
            href = title_elem.get_attribute("href") if title_elem else ""
            
            price_text = card.query_selector(".price").inner_text() if card.query_selector(".price") else "0"
            price = self._parse_price(price_text)

            location = card.query_selector(".location").inner_text() if card.query_selector(".location") else ""
            condition = card.query_selector(".condition").inner_text() if card.query_selector(".condition") else "good"
            seller = card.query_selector(".seller").inner_text() if card.query_selector(".seller") else ""

            listings.append(SourceListingSummary(
                external_id=ext_id,
                title=title,
                price=price,
                location=location,
                condition=condition,
                seller=seller,
                url=href
            ))
        return listings

    def extract_detail_page(self, page: Page, source_url: str) -> SourceListingDetail:
        ext_id = page.query_selector("#item-detail").get_attribute("data-id") or ""
        title = page.query_selector(".item-title").inner_text() if page.query_selector(".item-title") else ""
        price_text = page.query_selector(".item-price").inner_text() if page.query_selector(".item-price") else "0"
        price = self._parse_price(price_text)

        loc_text = page.query_selector(".item-location").inner_text() if page.query_selector(".item-location") else "São Paulo, SP"
        city, state = [x.strip() for x in loc_text.split(",")] if "," in loc_text else (loc_text, "SP")

        cond = page.query_selector(".item-condition").inner_text() if page.query_selector(".item-condition") else "good"
        seller_name = page.query_selector(".item-seller .name").inner_text() if page.query_selector(".item-seller .name") else "Vendedor"
        seller_rating = float(page.query_selector(".item-seller .rating").inner_text()) if page.query_selector(".item-seller .rating") else 5.0
        desc = page.query_selector(".item-description").inner_text() if page.query_selector(".item-description") else ""
        delivery = page.query_selector(".item-delivery")
        delivery_text = delivery.inner_text() if delivery else ""
        verification = page.query_selector(".item-seller .verified")
        seller_verification = "VERIFIED" if verification else "UNKNOWN"
        seller_evidence = [verification.inner_text()] if verification else []
        photos = []
        for image in page.query_selector_all(".item-gallery img")[:5]:
            photos.append({
                "url": image.get_attribute("src") or image.get_attribute("data-src") or "",
                "alt": image.get_attribute("alt") or "",
            })

        attrs = {}
        for li in page.query_selector_all(".item-attributes li"):
            k = li.get_attribute("data-key") or "attr"
            attrs[k] = li.inner_text()

        return SourceListingDetail(
            external_id=ext_id,
            title=title,
            description=desc,
            price=price,
            location_state=state,
            location_city=city,
            seller_name=seller_name,
            seller_rating=seller_rating,
            condition=cond,
            attributes=attrs,
            source_url=source_url,
            delivery_text=delivery_text,
            seller_verification=seller_verification,
            seller_evidence=seller_evidence,
            photos=photos,
        )

    def _parse_price(self, text: str) -> float:
        clean = re.sub(r"[^\d,\.]", "", text).replace(".", "").replace(",", ".")
        try:
            return float(clean)
        except ValueError:
            return 0.0

    async def search(self, query: SearchQuery) -> List[SourceListingSummary]:
        search_html = os.path.join(self.base_path, "search.html")
        if not os.path.exists(search_html):
            return []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{os.path.abspath(search_html)}")
            results = self.extract_search_page(page)
            browser.close()
            filtered = [
                r for r in results
                if query.query.lower() in r.title.lower()
                and (not (query.require_price or (query.min_price is not None and query.min_price > 0)) or (r.price is not None and r.price > 0))
                and (query.min_price is None or (r.price is not None and r.price >= query.min_price))
                and (query.max_price is None or (r.price is not None and r.price <= query.max_price))
            ]
            start = (query.page - 1) * query.limit
            return filtered[start:start + query.limit]

    async def fetch_listing(self, external_id: str) -> SourceListingDetail:
        item_html = os.path.join(self.base_path, "items", f"{external_id}.html")
        if not os.path.exists(item_html):
            raise FileNotFoundError(f"Fixture HTML {item_html} not found.")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{os.path.abspath(item_html)}")
            detail = self.extract_detail_page(page, source_url=f"file://{os.path.abspath(item_html)}")
            browser.close()
            return detail

    async def refresh_listing(self, external_id: str) -> SourceListingDetail:
        return await self.fetch_listing(external_id)

class OlxSource:
    """Opt-in read-only adapter backed by the browser-worker service."""

    def __init__(self, browser_worker_url: Optional[str] = None):
        self.mode = os.getenv("APP_MODE", "local").lower()
        self.external_providers = os.getenv("EXTERNAL_PROVIDERS", "disabled").lower()
        self.browser_worker_url = (
            browser_worker_url or os.getenv("BROWSER_WORKER_URL", "http://localhost:8100")
        ).rstrip("/")
        self.last_search_diagnostics: Dict[str, Any] = {}
        self.last_search_url = ""

    def _check_enabled(self):
        if self.mode != "live" or self.external_providers != "enabled":
            raise RuntimeError(
                "External provider 'olx' is disabled. Set APP_MODE=live and "
                "EXTERNAL_PROVIDERS=enabled, then create a manual browser session."
            )

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{self.browser_worker_url}{path}", json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                body = exc.response.json()
            except ValueError:
                body = {}
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            if isinstance(detail, dict):
                code = detail.get("code") or f"http_{exc.response.status_code}"
                msg = detail.get("message") or f"OLX browser-worker returned HTTP {exc.response.status_code}"
                kind = detail.get("kind") or ("parser" if exc.response.status_code == 502 else "guard" if exc.response.status_code == 429 else "operational")
                stage = detail.get("stage") or ("search" if "/search" in path else "detail")
                policy = detail.get("retry_policy") or (
                    "skipped_unverified" if code == "page_missing_required_fields"
                    else "retryable_after_parser_update" if code in {"parser_contract_changed", "olx_parser_mismatch"}
                    else "waiting_budget" if code == "olx_budget_exhausted"
                    else "waiting_session" if code == "olx_session_expired"
                    else "blocked" if code == "olx_access_blocked"
                    else "waiting_worker" if exc.response.status_code == 503
                    else "retryable_with_backoff" if exc.response.status_code == 504
                    else "none"
                )
                if code in {"olx_budget_exhausted", "olx_circuit_open", "olx_access_blocked", "olx_session_expired"}:
                    raise OlxAccessError(code, detail, exc.response.status_code) from exc
                raise MarketplaceOperationError(
                    code=code,
                    message=msg,
                    kind=kind,
                    stage=stage,
                    retry_policy=policy,
                    http_status=exc.response.status_code,
                    retry_after_seconds=detail.get("retry_after_seconds"),
                    missing_fields=detail.get("missing_fields"),
                    parser_version=detail.get("parser_version", "olx-dom-2026-08-24"),
                    safe_diagnostics_ref=detail.get("safe_diagnostics_ref"),
                    detail=detail,
                ) from exc
            raise MarketplaceOperationError(
                code=f"http_{exc.response.status_code}",
                message=f"OLX browser-worker returned HTTP {exc.response.status_code}: {exc}",
                http_status=exc.response.status_code,
            ) from exc
        except Exception as exc:
            if isinstance(exc, (MarketplaceOperationError, OlxAccessError)):
                raise
            raise MarketplaceOperationError(
                code="browser_worker_unavailable",
                message=f"OLX browser-worker connection failed: {exc}",
                kind="network",
                stage="search" if "/search" in path else "detail",
                retry_policy="waiting_worker",
            ) from exc

    async def search(self, query: SearchQuery) -> List[SourceListingSummary]:
        self._check_enabled()
        data = await self._post("/search", query.model_dump())
        self.last_search_diagnostics = data.get("diagnostics", {}) or {}
        self.last_search_url = data.get("url", "")
        return [SourceListingSummary(**item) for item in data.get("items", [])]

    async def fetch_listing(self, external_id: str) -> SourceListingDetail:
        self._check_enabled()
        data = await self._post("/listing", {"external_id": external_id})
        return SourceListingDetail(**data)

    async def refresh_listing(self, external_id: str) -> SourceListingDetail:
        return await self.fetch_listing(external_id)

def get_marketplace_source(source_type: Optional[str] = None) -> MarketplaceSource:
    st = source_type or os.getenv("MARKETPLACE_SOURCE", "fixture").lower()
    if st == "olx":
        return OlxSource()
    elif st == "playwright_fixture":
        return PlaywrightFixtureMarketplaceSource("fixtures/marketplace/html")
    return FixtureMarketplaceSource()
