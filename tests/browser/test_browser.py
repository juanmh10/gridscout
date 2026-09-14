import os
import pytest
from playwright.sync_api import sync_playwright
from packages.marketplace.playwright_source import PlaywrightFixtureMarketplaceSource

@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()

def test_extract_search_fixture(browser_context):
    fixture_path = os.path.abspath("fixtures/marketplace/html/search.html")
    assert os.path.exists(fixture_path), f"Fixture not found: {fixture_path}"

    page = browser_context.new_page()
    page.goto(f"file://{fixture_path}")

    source = PlaywrightFixtureMarketplaceSource(os.path.dirname(fixture_path))
    listings = source.extract_search_page(page)
    page.close()

    assert len(listings) == 3
    assert listings[0].external_id == "101"
    assert "RTX 3080" in listings[0].title
    assert listings[0].price == 2100.0
    assert listings[0].location == "São Paulo, SP"
    assert listings[0].seller == "TechTrader_SP"

def test_extract_detail_fixture(browser_context):
    fixture_path = os.path.abspath("fixtures/marketplace/html/items/101.html")
    assert os.path.exists(fixture_path), f"Fixture not found: {fixture_path}"

    page = browser_context.new_page()
    page.goto(f"file://{fixture_path}")

    source = PlaywrightFixtureMarketplaceSource(os.path.dirname(fixture_path))
    detail = source.extract_detail_page(page, source_url=f"file://{fixture_path}")
    page.close()

    assert detail.external_id == "101"
    assert "RTX 3080" in detail.title
    assert detail.price == 2100.0
    assert detail.location_city == "São Paulo"
    assert detail.location_state == "SP"
    assert detail.seller_name == "TechTrader_SP"
    assert detail.seller_rating == 4.9
    assert detail.condition == "like_new"
    assert detail.attributes.get("vram") == "10GB"
    assert "Placa usada apenas para jogos" in detail.description
