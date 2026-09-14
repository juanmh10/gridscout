"""Canonical hardware taxonomy version, enums, labels, and facet registry.

This module is the single source of truth for hardware categories, item forms,
classification statuses, exclusion codes, and category-specific facet definitions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional


HARDWARE_TAXONOMY_VERSION = "hardware-taxonomy-v2"
HARDWARE_SCHEMA_VERSION = "hardware-schema-v2"


class HardwareCategory(str, Enum):
    NOTEBOOK = "notebook"
    GPU = "gpu"
    CPU = "cpu"
    RAM = "ram"
    SSD = "ssd"
    MOTHERBOARD = "motherboard"
    DESKTOP = "desktop"
    CONSOLE = "console"
    PERIPHERAL = "peripheral"
    OTHER = "other"


class ItemForm(str, Enum):
    STANDALONE = "standalone"
    BUNDLE = "bundle"
    FULL_SYSTEM = "full_system"
    ACCESSORY = "accessory"
    PARTS = "parts"
    UNKNOWN = "unknown"


class ClassificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"
    LEGACY_UNVERIFIED = "legacy_unverified"


class ScopeMatchStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"
    COLLECTED = "collected"


class ExclusionCode(str, Enum):
    CATEGORY_MISMATCH = "CATEGORY_MISMATCH"
    BUNDLE_NOT_STANDALONE = "BUNDLE_NOT_STANDALONE"
    FULL_SYSTEM_NOT_COMPONENT = "FULL_SYSTEM_NOT_COMPONENT"
    PARTS_OR_BROKEN = "PARTS_OR_BROKEN"
    REQUIRED_ATTRIBUTE_UNKNOWN = "REQUIRED_ATTRIBUTE_UNKNOWN"
    FORM_FACTOR_MISMATCH = "FORM_FACTOR_MISMATCH"
    PRODUCT_UNRESOLVED = "PRODUCT_UNRESOLVED"
    LEGACY_WITHOUT_PROVENANCE = "LEGACY_WITHOUT_PROVENANCE"
    INVALID_PRICE = "INVALID_PRICE"
    UNVERIFIED_EVIDENCE = "UNVERIFIED_EVIDENCE"


CATEGORY_LABELS: Dict[HardwareCategory, str] = {
    HardwareCategory.NOTEBOOK: "Notebook",
    HardwareCategory.GPU: "Placa de Vídeo (GPU)",
    HardwareCategory.CPU: "Processador (CPU)",
    HardwareCategory.RAM: "Memória RAM",
    HardwareCategory.SSD: "Armazenamento (SSD)",
    HardwareCategory.MOTHERBOARD: "Placa-mãe",
    HardwareCategory.DESKTOP: "Desktop Completo",
    HardwareCategory.CONSOLE: "Console",
    HardwareCategory.PERIPHERAL: "Periférico",
    HardwareCategory.OTHER: "Outro",
}

ITEM_FORM_LABELS: Dict[ItemForm, str] = {
    ItemForm.STANDALONE: "Item Avulso",
    ItemForm.BUNDLE: "Kit / Combo",
    ItemForm.FULL_SYSTEM: "Computador Completo",
    ItemForm.ACCESSORY: "Acessório / Periférico",
    ItemForm.PARTS: "Peças / Defeituoso / Sucata",
    ItemForm.UNKNOWN: "Forma Desconhecida",
}

EXCLUSION_REASONS: Dict[str, str] = {
    ExclusionCode.CATEGORY_MISMATCH.value: "Categoria do anúncio incompatível com a coorte analítica.",
    ExclusionCode.BUNDLE_NOT_STANDALONE.value: "Item vendido em kit/combo; não elegível para coorte de componente avulso.",
    ExclusionCode.FULL_SYSTEM_NOT_COMPONENT.value: "Sistema completo anunciado; não elegível para precificação de componente avulso.",
    ExclusionCode.PARTS_OR_BROKEN.value: "Item anunciado para conserto, sucata ou com defeito explícito.",
    ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value: "Atributo obrigatório para identificação da coorte não comprovado no anúncio.",
    ExclusionCode.FORM_FACTOR_MISMATCH.value: "Formato físico incompatível (ex: SODIMM em UDIMM).",
    ExclusionCode.PRODUCT_UNRESOLVED.value: "Produto ou modelo canônico exato não resolvido.",
    ExclusionCode.LEGACY_WITHOUT_PROVENANCE.value: "Registro histórico sem evidência temporal comprovada.",
    ExclusionCode.INVALID_PRICE.value: "Preço inválido ou fora dos limites aceitáveis.",
    ExclusionCode.UNVERIFIED_EVIDENCE.value: "Evidência textual insuficiente para confirmação analítica.",
}


# Category-specific facet registry defining valid facet parameters for each category
CATEGORY_FACETS: Dict[str, List[Dict[str, Any]]] = {
    HardwareCategory.NOTEBOOK.value: [
        {"key": "brand", "label": "Marca", "type": "string"},
        {"key": "family", "label": "Família / Linha", "type": "string"},
        {"key": "model", "label": "Modelo", "type": "string"},
        {"key": "cpu_brand", "label": "Fabricante CPU", "type": "string", "options": ["Intel", "AMD", "Apple"]},
        {"key": "cpu_model", "label": "Modelo CPU", "type": "string"},
        {"key": "ram_gb", "label": "Memória RAM (GB)", "type": "number", "options": [8, 16, 24, 32, 64]},
        {"key": "storage_gb", "label": "Armazenamento (GB)", "type": "number", "options": [256, 512, 1000, 2000]},
        {"key": "panel_type", "label": "Painel da Tela", "type": "string", "options": ["IPS", "WVA", "OLED", "TN"]},
        {"key": "screen_resolution", "label": "Resolução", "type": "string", "options": ["FHD", "2.5K", "4K"]},
        {"key": "gpu", "label": "Placa de Vídeo Dedicada", "type": "string"},
    ],
    HardwareCategory.GPU.value: [
        {"key": "chip_brand", "label": "Fabricante do Chip", "type": "string", "options": ["NVIDIA", "AMD", "Intel"]},
        {"key": "chip_model", "label": "Modelo do Chip / GPU", "type": "string"},
        {"key": "vram_gb", "label": "VRAM (GB)", "type": "number", "options": [4, 6, 8, 10, 12, 16, 20, 24]},
        {"key": "memory_type", "label": "Tipo de Memória", "type": "string", "options": ["GDDR6X", "GDDR6", "GDDR5"]},
        {"key": "aib_partner", "label": "Fabricante / Montadora (AIB)", "type": "string", "options": ["ASUS", "MSI", "Gigabyte", "Galax", "EVGA", "Zotac", "PowerColor", "Sapphire", "XFX", "Gainward", "Palit", "PNY"]},
        {"key": "model_line", "label": "Linha do Modelo", "type": "string"},
    ],
    HardwareCategory.CPU.value: [
        {"key": "brand", "label": "Marca", "type": "string", "options": ["AMD", "Intel"]},
        {"key": "family", "label": "Família", "type": "string", "options": ["Ryzen 5", "Ryzen 7", "Ryzen 9", "Ryzen 3", "Core i5", "Core i7", "Core i9", "Core i3", "Core Ultra 5", "Core Ultra 7", "Core Ultra 9", "Xeon", "EPYC"]},
        {"key": "model_number", "label": "Número / Modelo Exato", "type": "string"},
        {"key": "generation", "label": "Geração / Série", "type": "string"},
        {"key": "socket", "label": "Socket", "type": "string", "options": ["AM4", "AM5", "LGA1700", "LGA1851", "LGA1200"]},
        {"key": "suffix", "label": "Sufixo", "type": "string", "options": ["X", "X3D", "F", "K", "KF", "G", "T", "None"]},
        {"key": "integrated_gpu", "label": "Vídeo Integrado", "type": "boolean"},
    ],
    HardwareCategory.RAM.value: [
        {"key": "brand", "label": "Marca", "type": "string"},
        {"key": "ddr_standard", "label": "Padrão DDR", "type": "string", "options": ["DDR5", "DDR4", "DDR3"]},
        {"key": "total_capacity_gb", "label": "Capacidade Total (GB)", "type": "number", "options": [8, 16, 32, 64, 128]},
        {"key": "form_factor", "label": "Formato", "type": "string", "options": ["UDIMM", "SODIMM", "RDIMM"]},
        {"key": "frequency_mhz", "label": "Frequência (MHz)", "type": "number", "options": [2666, 3200, 3600, 4800, 5200, 5600, 6000, 6400]},
        {"key": "ecc_support", "label": "Suporte ECC", "type": "boolean"},
        {"key": "module_count", "label": "Quantidade de Módulos (Pentes)", "type": "number", "options": [1, 2, 4]},
    ],
    HardwareCategory.SSD.value: [
        {"key": "brand", "label": "Marca", "type": "string"},
        {"key": "capacity_gb", "label": "Capacidade (GB)", "type": "number", "options": [240, 480, 500, 512, 1000, 2000, 4000]},
        {"key": "interface", "label": "Interface", "type": "string", "options": ["NVMe PCIe 4.0", "NVMe PCIe 3.0", "NVMe PCIe 5.0", "SATA III"]},
        {"key": "form_factor", "label": "Formato", "type": "string", "options": ["M.2 2280", "M.2 2230", "2.5 polegadas"]},
        {"key": "performance_family", "label": "Linha de Desempenho", "type": "string"},
    ],
    HardwareCategory.MOTHERBOARD.value: [
        {"key": "brand", "label": "Marca", "type": "string"},
        {"key": "socket", "label": "Socket", "type": "string"},
        {"key": "chipset", "label": "Chipset", "type": "string"},
        {"key": "form_factor", "label": "Formato", "type": "string", "options": ["ATX", "Micro-ATX", "Mini-ITX", "E-ATX"]},
    ],
    HardwareCategory.DESKTOP.value: [
        {"key": "brand", "label": "Montadora / Marca", "type": "string"},
        {"key": "cpu", "label": "Processador", "type": "string"},
        {"key": "gpu", "label": "Placa de Vídeo", "type": "string"},
        {"key": "ram_gb", "label": "RAM (GB)", "type": "number"},
        {"key": "storage_gb", "label": "Armazenamento (GB)", "type": "number"},
    ],
    HardwareCategory.CONSOLE.value: [
        {"key": "brand", "label": "Marca", "type": "string", "options": ["Sony", "Microsoft", "Nintendo", "Valve"]},
        {"key": "model", "label": "Modelo", "type": "string"},
        {"key": "storage_capacity", "label": "Armazenamento", "type": "string"},
        {"key": "edition", "label": "Edição", "type": "string"},
    ],
    HardwareCategory.PERIPHERAL.value: [
        {"key": "brand", "label": "Marca", "type": "string"},
        {"key": "type", "label": "Tipo", "type": "string", "options": ["Teclado", "Mouse", "Headset", "Monitor", "Controle", "Volante"]},
        {"key": "model", "label": "Modelo", "type": "string"},
    ],
    HardwareCategory.OTHER.value: [
        {"key": "brand", "label": "Marca", "type": "string"},
        {"key": "model", "label": "Modelo", "type": "string"},
    ],
}


def normalize_category(value: Any) -> HardwareCategory:
    """Safely cast or fold any string into a canonical HardwareCategory."""
    if isinstance(value, HardwareCategory):
        return value
    text = str(value or "").strip().lower()
    mapping = {
        "notebook": HardwareCategory.NOTEBOOK,
        "laptop": HardwareCategory.NOTEBOOK,
        "laptops": HardwareCategory.NOTEBOOK,
        "notebooks": HardwareCategory.NOTEBOOK,
        "gpu": HardwareCategory.GPU,
        "gpus": HardwareCategory.GPU,
        "placa de video": HardwareCategory.GPU,
        "placa de vídeo": HardwareCategory.GPU,
        "placa_de_video": HardwareCategory.GPU,
        "graphics card": HardwareCategory.GPU,
        "cpu": HardwareCategory.CPU,
        "cpus": HardwareCategory.CPU,
        "processador": HardwareCategory.CPU,
        "processadores": HardwareCategory.CPU,
        "processor": HardwareCategory.CPU,
        "ram": HardwareCategory.RAM,
        "memoria": HardwareCategory.RAM,
        "memória": HardwareCategory.RAM,
        "memoria ram": HardwareCategory.RAM,
        "ssd": HardwareCategory.SSD,
        "ssds": HardwareCategory.SSD,
        "motherboard": HardwareCategory.MOTHERBOARD,
        "placa mae": HardwareCategory.MOTHERBOARD,
        "placa-mãe": HardwareCategory.MOTHERBOARD,
        "placa mãe": HardwareCategory.MOTHERBOARD,
        "desktop": HardwareCategory.DESKTOP,
        "pc": HardwareCategory.DESKTOP,
        "computador": HardwareCategory.DESKTOP,
        "console": HardwareCategory.CONSOLE,
        "videogame": HardwareCategory.CONSOLE,
        "video game": HardwareCategory.CONSOLE,
        "peripheral": HardwareCategory.PERIPHERAL,
        "periferico": HardwareCategory.PERIPHERAL,
        "periférico": HardwareCategory.PERIPHERAL,
        "acessorio": HardwareCategory.PERIPHERAL,
        "other": HardwareCategory.OTHER,
        "outros": HardwareCategory.OTHER,
    }
    return mapping.get(text, HardwareCategory.OTHER)


def supports_market_analysis(value: Any) -> bool:
    """Whether a category has a comparable economic unit for price analysis.

    ``other`` preserves a broad-collection observation without pretending it
    belongs to a hardware cohort. It must remain visible in the workspace, but
    cannot contribute to price statistics or opportunities.
    """
    return True


def normalize_item_form(value: Any) -> ItemForm:
    """Safely cast or fold string into ItemForm."""
    if isinstance(value, ItemForm):
        return value
    text = str(value or "").strip().lower()
    mapping = {
        "standalone": ItemForm.STANDALONE,
        "avulso": ItemForm.STANDALONE,
        "single": ItemForm.STANDALONE,
        "bundle": ItemForm.BUNDLE,
        "kit": ItemForm.BUNDLE,
        "combo": ItemForm.BUNDLE,
        "full_system": ItemForm.FULL_SYSTEM,
        "completo": ItemForm.FULL_SYSTEM,
        "pc_completo": ItemForm.FULL_SYSTEM,
        "accessory": ItemForm.ACCESSORY,
        "acessorio": ItemForm.ACCESSORY,
        "periferico": ItemForm.ACCESSORY,
        "parts": ItemForm.PARTS,
        "pecas": ItemForm.PARTS,
        "peças": ItemForm.PARTS,
        "sucata": ItemForm.PARTS,
        "defeito": ItemForm.PARTS,
        "for_parts": ItemForm.PARTS,
        "unknown": ItemForm.UNKNOWN,
        "desconhecido": ItemForm.UNKNOWN,
    }
    return mapping.get(text, ItemForm.UNKNOWN)
