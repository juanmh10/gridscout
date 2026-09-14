from packages.catalog import ensure_notebook_catalog, find_catalog_notebook
from packages.core.database import Base
from packages.core.models import Listing, PipelineRun, PipelineRunScopeItem, Product, Profile
from packages.marketplace.source import SourceListingDetail, SourceListingSummary
from packages.pipeline import runner
from packages.search import compile_search_local, prepare_pipeline_scopes
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest


def test_notebook_catalog_import_merges_source_duplicates_and_identifies_sku():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        summary = ensure_notebook_catalog(session)

        assert summary == {"products": 342, "rows": 342}
        assert session.query(Product).filter(Product.category == "notebook").count() == 342
        matched = find_catalog_notebook(
            session,
            title="Lenovo IdeaPad 1 15AMN7 82X50004BR impecável",
        )
        assert matched is not None
        assert matched.display_name == "Lenovo IdeaPad 1 15AMN7 82X50004BR"
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_price_only_notebook_scope_uses_catalog_without_expanding_navigation():
    plan = compile_search_local("notebook até R$ 2.500").plan

    scopes = prepare_pipeline_scopes(plan, marketplace="olx")

    assert len(scopes) == 1
    assert scopes[0]["query"] == "notebook"
    assert scopes[0]["catalog_match"] == "notebook-brasil-v1"
    assert sum(scope["limit"] for scope in scopes) == plan.max_results == 30


class _CatalogListingSource:
    async def search(self, query):
        return [SourceListingSummary(
            external_id="notebook-1",
            title="Notebook Lenovo IdeaPad 1 15AMN7 82X50004BR",
            price=2300.0,
            url="https://example.test/notebook-1",
        )]

    async def fetch_listing(self, external_id):
        return SourceListingDetail(
            external_id=external_id,
            title="Notebook Lenovo IdeaPad 1 15AMN7 82X50004BR",
            description="Ryzen 5, 8 GB RAM e SSD 256 GB.",
            price=2300.0,
            condition="used",
            attributes={"category": "notebook", "ram_gb": 8, "storage_gb": 256},
            source_url="https://example.test/notebook-1",
        )


@pytest.mark.asyncio
async def test_pipeline_assigns_confirmed_catalog_model_after_broad_notebook_retrieval(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        plan = compile_search_local("notebook até R$ 2.500").plan
        scope = prepare_pipeline_scopes(plan, marketplace="fixture")[0]
        profile = Profile(id="catalog-profile", name="Catálogo", name_normalized="catalogo")
        run = PipelineRun(
            id="catalog-run",
            profile_id=profile.id,
            status="pending",
            steps=[{"configuration": {"scopes": [scope]}}],
        )
        db.add_all([profile, run])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(runner, "SessionLocal", sessions)
    monkeypatch.setattr(runner, "get_marketplace_source", lambda *_: _CatalogListingSource())
    result = await runner.run_pipeline("catalog-run", source_type="fixture", model_mode="local")

    assert result["processed"] == 1
    check = sessions()
    try:
        listing = check.query(Listing).filter(Listing.external_id == "notebook-1").one()
        assert listing.product is not None
        assert listing.product.display_name == "Lenovo IdeaPad 1 15AMN7 82X50004BR"
        item = check.query(PipelineRunScopeItem).filter(PipelineRunScopeItem.external_id == "notebook-1").one()
        assert item.match_status == "confirmed"
        assert item.match_details["catalog"] == {
            "status": "confirmed",
            "product_name": "Lenovo IdeaPad 1 15AMN7 82X50004BR",
        }
        assert check.query(PipelineRun).filter(PipelineRun.id == "catalog-run").one().status == "completed"
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_notebook_catalog_resolves_specific_models():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        ensure_notebook_catalog(session)
        
        # Insert Dell Vostro 5481 manually since it might not be in the default dataset
        vostro = Product(
            id="catalog-notebook-vostro-5481",
            category="notebook",
            brand="Dell",
            family="Vostro",
            model="5481",
            display_name="Dell Vostro 5481",
            attributes={
                "catalog_mode": "notebook-brasil-v1",
                "catalog_aliases": ["5481"]
            }
        )
        session.add(vostro)
        session.flush()

        matched = find_catalog_notebook(
            session,
            title="Notebook Dell Latitude 5440 i5-1345U 16GB 256GB NvMe 14 FHD IPS Tec R iluminado Biometria",
        )
        assert matched is not None
        assert "5440" in matched.model

        matched2 = find_catalog_notebook(
            session,
            title="Notebook Dell Vostro 5481 i7 16GB RAM + SSD 256GB + HD 1TB + MX130 2GB Full HD IPS",
        )
        assert matched2 is not None
        assert "5481" in matched2.model

        matched3 = find_catalog_notebook(
            session,
            title="Acer Nitro V 15 ANV15-52-514Z",
        )
        assert matched3 is not None
        assert "Nitro" in matched3.family

        matched4 = find_catalog_notebook(
            session,
            title="Acer Nitro 5",
        )
        assert matched4 is not None
        assert "Nitro" in matched4.family
        
        matched5 = find_catalog_notebook(
            session,
            title="Lenovo IdeaPad 1 15AMN7 82X50004BR impecável",
        )
        assert matched5 is not None
        assert matched5.display_name == "Lenovo IdeaPad 1 15AMN7 82X50004BR"
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
