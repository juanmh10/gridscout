import pytest
from packages.core.hardware_taxonomy import (
    HardwareCategory, ItemForm, ClassificationStatus, ScopeMatchStatus, ExclusionCode,
    CATEGORY_LABELS, CATEGORY_FACETS, normalize_category
)
from packages.core.hardware_schemas import (
    NotebookAttributes, GPUAttributes, CPUAttributes, RAMAttributes,
    SSDAttributes, MotherboardAttributes, DesktopAttributes, OtherAttributes,
    ClassificationResult, EligibilityEvaluationResult,
)
from packages.classification.scope_guard import pre_model_scope_guard
from packages.classification.classifier import classify_listing_text


def test_taxonomy_enums_and_normalization():
    assert HardwareCategory.GPU.value == "gpu"
    assert HardwareCategory.CPU.value == "cpu"
    assert HardwareCategory.NOTEBOOK.value == "notebook"
    assert HardwareCategory.RAM.value == "ram"

    assert normalize_category("placa de video") == HardwareCategory.GPU
    assert normalize_category("processador") == HardwareCategory.CPU
    assert normalize_category("memoria") == HardwareCategory.RAM
    assert normalize_category("notebook") == HardwareCategory.NOTEBOOK
    assert normalize_category("something_unknown") == HardwareCategory.OTHER

    assert "gpu" in CATEGORY_FACETS
    assert "cpu" in CATEGORY_FACETS
    assert "ram" in CATEGORY_FACETS
    assert "notebook" in CATEGORY_FACETS


def test_hardware_schemas_validation():
    gpu_attr = GPUAttributes(chipset="RTX 3060", vram_gb=12, memory_type="GDDR6")
    assert gpu_attr.category == "gpu"
    assert gpu_attr.vram_gb == 12

    cpu_attr = CPUAttributes(model="Ryzen 5 5600", socket="AM4", cores=6, threads=12)
    assert cpu_attr.category == "cpu"
    assert cpu_attr.socket == "AM4"

    ram_attr = RAMAttributes(ram_generation="DDR4", capacity_gb=16, form_factor="UDIMM", speed_mhz=3200)
    assert ram_attr.category == "ram"
    assert ram_attr.form_factor == "UDIMM"


def test_pre_model_scope_guard_rejections():
    allowed, code, form, cat = pre_model_scope_guard(
        title="RTX 3070 com artefatos para sucata",
        description="",
        target_category=HardwareCategory.GPU,
    )
    assert not allowed
    assert code == ExclusionCode.PARTS_OR_BROKEN.value
    assert form == ItemForm.PARTS

    allowed_bundle, code_bundle, form_bundle, _ = pre_model_scope_guard(
        title="Kit Upgrade Ryzen 5600 + Placa Mãe B450 + 16GB RAM",
        description="",
        target_category=HardwareCategory.CPU,
    )
    assert not allowed_bundle
    assert code_bundle == ExclusionCode.BUNDLE_NOT_STANDALONE.value
    assert form_bundle == ItemForm.BUNDLE

    allowed_sys, code_sys, form_sys, _ = pre_model_scope_guard(
        title="PC Gamer Completo i5 RTX 3060 16GB",
        description="",
        target_category=HardwareCategory.GPU,
    )
    assert not allowed_sys
    assert code_sys == ExclusionCode.FULL_SYSTEM_NOT_COMPONENT.value
    assert form_sys == ItemForm.FULL_SYSTEM


def test_classifier_gpu_identification():
    res = classify_listing_text(
        title="Placa de Video Galax NVIDIA GeForce RTX 3060 12GB GDDR6",
        description="Placa seminova, funcionando 100%, acompanha caixa",
    )
    assert res.category == HardwareCategory.GPU
    assert res.status == ClassificationStatus.CONFIRMED
    assert "RTX 3060" in res.model
    assert res.attributes.get("vram_gb") == 12
    assert res.attributes.get("chipset") == "RTX 3060"


def test_classifier_cpu_identification():
    res = classify_listing_text(
        title="Processador AMD Ryzen 7 5800X AM4 8C/16T",
        description="Excelente estado, sem pinos tortos",
    )
    assert res.category == HardwareCategory.CPU
    assert res.status == ClassificationStatus.CONFIRMED
    assert "Ryzen 7 5800X" in res.model
    assert res.attributes.get("socket") == "AM4"


def test_classifier_ram_sodimm_isolation():
    # RAM for notebook must NOT be classified as a Notebook!
    res = classify_listing_text(
        title="Memória RAM Kingston Fury Impact 16GB DDR4 3200MHz Para Notebook SODIMM",
        description="Memória para notebook funcionando",
    )
    assert res.category == HardwareCategory.RAM
    assert res.status == ClassificationStatus.CONFIRMED
    assert res.attributes.get("form_factor") == "SODIMM"
    assert res.attributes.get("capacity_gb") == 16
    assert res.attributes.get("ram_generation") == "DDR4"


def test_classifier_keeps_an_uncovered_broad_collection_item_in_other():
    res = classify_listing_text(
        title="Câmera Canon EOS Rebel com lente",
        description="Câmera fotográfica usada em perfeito funcionamento.",
    )

    assert res.category == HardwareCategory.OTHER
    assert res.status == ClassificationStatus.UNVERIFIED
    assert any("classificado como Outro" in evidence for evidence in res.evidence)


def test_classifier_ps5_console_identification():
    """A supported game console belongs in CONSOLE, not OTHER."""
    res = classify_listing_text(
        title="PlayStation 5 Slim 1TB",
        description="Console PS5 Slim novo com um controle.",
    )
    assert res.category == HardwareCategory.CONSOLE
    assert res.brand == "Sony"
    assert "PlayStation 5" in res.model or "PS5" in res.model
    assert res.status == ClassificationStatus.CONFIRMED


def test_classifier_xbox_and_nintendo_console_identification():
    """Xbox and Nintendo consoles belong in CONSOLE."""
    res_xbox = classify_listing_text("Xbox Series S 512GB", "Seminovo")
    assert res_xbox.category == HardwareCategory.CONSOLE
    assert res_xbox.brand == "Microsoft"
    res_switch = classify_listing_text("Nintendo Switch OLED", "Na caixa")
    assert res_switch.category == HardwareCategory.CONSOLE
    assert res_switch.brand == "Nintendo"
    assert "Nintendo Switch" in res_switch.model
    assert res_switch.status == ClassificationStatus.CONFIRMED


def test_other_category_analytics_eligibility():
    from packages.classification.eligibility import evaluate_analytics_eligibility

    res = evaluate_analytics_eligibility(
        category=HardwareCategory.OTHER,
        item_form=ItemForm.STANDALONE,
        classification_status=ClassificationStatus.CONFIRMED,
        price=2800.0,
        condition="used",
    )
    assert res.analytics_eligible is True
    assert res.exclusion_codes == []


def test_notebook_not_misclassified_as_gpu_ram_cpu_or_ssd():
    """Notebook listings mentioning components must be classified as NOTEBOOK and FULL_SYSTEM."""
    # Acer Nitro with GPU and RAM specs
    res_nitro = classify_listing_text(
        title="Notebook Gamer Acer Nitro 5 RTX 3060 16GB RAM 512GB SSD Tela IPS 144Hz",
        description="Notebook em ótimo estado, roda tudo, acompanha carregador original",
    )
    assert res_nitro.category == HardwareCategory.NOTEBOOK
    assert res_nitro.item_form == ItemForm.FULL_SYSTEM
    assert res_nitro.status == ClassificationStatus.CONFIRMED

    # Dell Inspiron with RAM and SSD specs
    res_dell = classify_listing_text(
        title="Notebook Dell Inspiron 15 i5 8GB RAM 256GB SSD",
        description="Notebook usado para trabalho",
    )
    assert res_dell.category == HardwareCategory.NOTEBOOK
    assert res_dell.item_form == ItemForm.FULL_SYSTEM

    # Lenovo IdeaPad with Ryzen CPU specs
    res_lenovo = classify_listing_text(
        title="Notebook Lenovo IdeaPad 3 Ryzen 5 5500U 8GB",
        description="Notebook seminovo com tela 15.6 pol",
    )
    assert res_lenovo.category == HardwareCategory.NOTEBOOK
    assert res_lenovo.item_form == ItemForm.FULL_SYSTEM

    # ASUS TUF Gaming
    res_tuf = classify_listing_text(
        title="Notebook Asus TUF Gaming RTX 3050 16GB RAM",
        description="Notebook gamer impecável",
    )
    assert res_tuf.category == HardwareCategory.NOTEBOOK
    assert res_tuf.item_form == ItemForm.FULL_SYSTEM


def test_scope_guard_blocks_notebooks_from_gpu_and_ram_targets():
    """Scope guard must reject notebook listings for GPU and RAM target categories."""
    allowed_gpu, code_gpu, form_gpu, _ = pre_model_scope_guard(
        title="Notebook Acer Nitro 5 RTX 3060 16GB RAM",
        description="",
        target_category=HardwareCategory.GPU,
    )
    assert not allowed_gpu
    assert code_gpu == ExclusionCode.FULL_SYSTEM_NOT_COMPONENT.value

    allowed_ram, code_ram, form_ram, _ = pre_model_scope_guard(
        title="Notebook Dell Inspiron 15 16GB RAM DDR4",
        description="",
        target_category=HardwareCategory.RAM,
    )
    assert not allowed_ram
    assert code_ram == ExclusionCode.FULL_SYSTEM_NOT_COMPONENT.value



