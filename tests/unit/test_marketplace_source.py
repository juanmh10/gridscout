import os
import pytest
from packages.marketplace.source import (
    SearchQuery,
    SourceListingSummary,
    SourceListingDetail,
    FixtureMarketplaceSource,
    PlaywrightFixtureMarketplaceSource,
    OlxSource,
    get_marketplace_source
)

def test_marketplace_source_factory():
    default_source = get_marketplace_source()
    assert isinstance(default_source, FixtureMarketplaceSource)

    olx_source = get_marketplace_source("olx")
    assert isinstance(olx_source, OlxSource)

    pw_source = get_marketplace_source("playwright_fixture")
    assert isinstance(pw_source, PlaywrightFixtureMarketplaceSource)

@pytest.mark.asyncio
async def test_olx_source_mvp_disabled_guard():
    olx = OlxSource()
    # In local MVP mode, any live action must fail fast with descriptive error
    with pytest.raises(RuntimeError) as exc_info:
        await olx.search(SearchQuery(query="RTX 3080"))
    assert "APP_MODE=live" in str(exc_info.value)

    with pytest.raises(RuntimeError) as exc_info:
        await olx.fetch_listing("olx-12345")
    assert "APP_MODE=live" in str(exc_info.value)

    with pytest.raises(RuntimeError) as exc_info:
        await olx.refresh_listing("olx-12345")
    assert "APP_MODE=live" in str(exc_info.value)

@pytest.mark.asyncio
async def test_fixture_marketplace_source():
    source = FixtureMarketplaceSource()
    # Test price parsing helper
    pw_source = PlaywrightFixtureMarketplaceSource("fixtures/marketplace/html")
    assert pw_source._parse_price("R$ 2.100,00") == 2100.0
    assert pw_source._parse_price("R$ 3.500") == 3500.0
    assert pw_source._parse_price("invalid") == 0.0
    detail = await source.fetch_listing("101")
    assert detail.delivery_text == "Envio disponível para todo o Brasil"
    assert detail.seller_verification == "VERIFIED"
    assert detail.seller_evidence == ["Usuário verificado"]


@pytest.mark.asyncio
async def test_fixture_search_honors_paginated_offset():
    source = FixtureMarketplaceSource()
    first = await source.search(SearchQuery(query="", limit=1, page=1))
    second = await source.search(SearchQuery(query="", limit=1, page=2))
    assert first[0].external_id == "101"
    assert second[0].external_id == "102"
