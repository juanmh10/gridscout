import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from packages.core.models import Base, Product, MarketCohort
from packages.core.hardware_taxonomy import HardwareCategory, ItemForm, ClassificationStatus, ExclusionCode
from packages.classification.eligibility import evaluate_analytics_eligibility
from packages.catalog.gpu import ensure_gpu_catalog, resolve_market_cohort as resolve_gpu_cohort
from packages.catalog.cpu import ensure_cpu_catalog, resolve_market_cohort as resolve_cpu_cohort
from packages.catalog.ram import ensure_ram_catalog, resolve_market_cohort as resolve_ram_cohort
from packages.catalog.resolver import resolve_product_and_cohort


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_eligibility_evaluation():
    # Valid GPU
    res_valid = evaluate_analytics_eligibility(
        category=HardwareCategory.GPU,
        item_form=ItemForm.STANDALONE,
        classification_status=ClassificationStatus.CONFIRMED,
        price=1800.0,
        condition="used",
        attributes={"chipset": "RTX 3060", "vram_gb": 12},
    )
    assert res_valid.analytics_eligible
    assert len(res_valid.exclusion_codes) == 0

    # Defective / parts GPU
    res_broken = evaluate_analytics_eligibility(
        category=HardwareCategory.GPU,
        item_form=ItemForm.STANDALONE,
        classification_status=ClassificationStatus.CONFIRMED,
        price=300.0,
        condition="for_parts",
        attributes={"chipset": "RTX 3060", "vram_gb": 12},
    )
    assert not res_broken.analytics_eligible
    assert ExclusionCode.PARTS_OR_BROKEN.value in res_broken.exclusion_codes

    # Unverified status
    res_unverified = evaluate_analytics_eligibility(
        category=HardwareCategory.GPU,
        item_form=ItemForm.STANDALONE,
        classification_status=ClassificationStatus.UNVERIFIED,
        price=1800.0,
        condition="used",
        attributes={"chipset": "RTX 3060", "vram_gb": 12},
    )
    assert not res_unverified.analytics_eligible
    assert ExclusionCode.UNVERIFIED_EVIDENCE.value in res_unverified.exclusion_codes

    # Missing mandatory attribute (VRAM for GPU)
    res_no_vram = evaluate_analytics_eligibility(
        category=HardwareCategory.GPU,
        item_form=ItemForm.STANDALONE,
        classification_status=ClassificationStatus.CONFIRMED,
        price=1800.0,
        condition="used",
        attributes={"chipset": "RTX 3060"},
    )
    assert not res_no_vram.analytics_eligible
    assert ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value in res_no_vram.exclusion_codes


def test_gpu_catalog_and_cohort_resolution(db_session):
    ensure_gpu_catalog(db_session)
    cohort = resolve_gpu_cohort(db_session, title="RTX 3060 12GB", attributes={"chip_model": "RTX 3060", "vram_gb": 12})
    assert cohort is not None
    assert "RTX 3060" in cohort.display_name
    assert "12GB" in cohort.display_name

    prod, coh, reason = resolve_product_and_cohort(
        db_session,
        category="gpu",
        title="Placa de Video Galax RTX 3060 12GB GDDR6",
        description="",
        attributes={"chipset": "RTX 3060", "vram_gb": 12},
    )
    assert coh is not None
    assert coh.id == cohort.id


def test_cpu_catalog_and_cohort_resolution(db_session):
    ensure_cpu_catalog(db_session)
    cohort = resolve_cpu_cohort(db_session, title="Ryzen 5 5600", attributes={"model": "Ryzen 5 5600", "socket": "AM4"})
    assert cohort is not None
    assert "Ryzen 5 5600" in cohort.display_name


def test_ram_catalog_and_cohort_resolution(db_session):
    ensure_ram_catalog(db_session)
    cohort = resolve_ram_cohort(db_session, title="DDR4 16GB 3200MHz", attributes={"ram_generation": "DDR4", "capacity_gb": 16, "form_factor": "UDIMM", "speed_mhz": 3200})
    assert cohort is not None
    assert "16GB" in cohort.display_name
    assert "DDR4" in cohort.display_name
