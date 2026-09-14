"""Discriminated Pydantic schemas for hardware attributes and classification contracts.

All attributes maintain strict versioning, explicit unknown values (None, not False),
and category-specific validated fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

from packages.core.hardware_taxonomy import (
    HARDWARE_SCHEMA_VERSION,
    HARDWARE_TAXONOMY_VERSION,
    ClassificationStatus,
    HardwareCategory,
    ItemForm,
    ScopeMatchStatus,
)


class BaseHardwareAttributes(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    schema_version: str = HARDWARE_SCHEMA_VERSION


class NotebookAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.NOTEBOOK] = HardwareCategory.NOTEBOOK
    brand: Optional[str] = None
    family: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    cpu_brand: Optional[str] = None
    cpu_model: Optional[str] = None
    cpu_generation: Optional[str] = None
    gpu: Optional[str] = None
    ram_gb: Optional[float] = None
    ram_type: Optional[str] = None
    storage_gb: Optional[float] = None
    storage_type: Optional[str] = None
    screen_size_inches: Optional[float] = None
    screen_resolution: Optional[str] = None
    panel_type: Optional[str] = None
    screen_ips: Optional[bool] = None
    wifi_standard: Optional[str] = None
    wifi_5ghz_or_better: Optional[bool] = None
    keyboard_abnt2: Optional[bool] = None
    operating_system: Optional[str] = None


class GPUAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.GPU] = HardwareCategory.GPU
    chip_brand: Optional[str] = None  # NVIDIA, AMD, Intel
    chip_model: Optional[str] = None  # e.g., RTX 3080, RX 6700 XT
    vram_gb: Optional[float] = None  # e.g., 10, 12, 16, 24
    memory_type: Optional[str] = None  # GDDR6X, GDDR6, etc.
    aib_partner: Optional[str] = None  # ASUS, MSI, Gigabyte, Galax, EVGA, etc.
    model_line: Optional[str] = None  # ROG Strix, Gaming X, TUF, etc.
    bus_width_bits: Optional[int] = None  # 192, 256, 320, 384
    tdp_watts: Optional[float] = None


class CPUAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.CPU] = HardwareCategory.CPU
    brand: Optional[str] = None  # AMD, Intel
    family: Optional[str] = None  # Ryzen 5, Ryzen 7, Core i5, Core i7, etc.
    model_number: Optional[str] = None  # 5600X, 5800X3D, 13700K
    generation: Optional[str] = None  # 5000, 7000, 13th Gen, etc.
    socket: Optional[str] = None  # AM4, AM5, LGA1700, etc.
    cores: Optional[int] = None
    threads: Optional[int] = None
    base_clock_ghz: Optional[float] = None
    boost_clock_ghz: Optional[float] = None
    suffix: Optional[str] = None  # X, X3D, K, KF, F, G, None
    integrated_gpu: Optional[bool] = None


class RAMAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.RAM] = HardwareCategory.RAM
    brand: Optional[str] = None
    line: Optional[str] = None  # Fury Beast, Vengeance, Trident Z
    total_capacity_gb: Optional[float] = None  # e.g. 16, 32
    ddr_standard: Optional[str] = None  # DDR4, DDR5, DDR3
    form_factor: Optional[str] = None  # UDIMM (Desktop), SODIMM (Notebook), RDIMM (Server)
    frequency_mhz: Optional[float] = None  # 3200, 3600, 5600, 6000
    ecc_support: Optional[bool] = None  # Crucial: None if unknown, never default False
    module_count: Optional[int] = None  # 1, 2, 4
    capacity_per_module_gb: Optional[float] = None


class SSDAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.SSD] = HardwareCategory.SSD
    brand: Optional[str] = None
    line: Optional[str] = None  # 980 Pro, SN850X, Kingston NV2
    capacity_gb: Optional[float] = None
    interface: Optional[str] = None  # NVMe PCIe 4.0, NVMe PCIe 3.0, SATA III
    form_factor: Optional[str] = None  # M.2 2280, 2.5 polegadas
    performance_family: Optional[str] = None


class MotherboardAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.MOTHERBOARD] = HardwareCategory.MOTHERBOARD
    brand: Optional[str] = None
    socket: Optional[str] = None
    chipset: Optional[str] = None  # B550, X570, B650, B760, Z790
    form_factor: Optional[str] = None  # ATX, Micro-ATX, Mini-ITX
    ram_slots: Optional[int] = None
    ddr_standard: Optional[str] = None


class DesktopAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.DESKTOP] = HardwareCategory.DESKTOP
    brand: Optional[str] = None
    cpu: Optional[str] = None
    gpu: Optional[str] = None
    ram_gb: Optional[float] = None
    storage_gb: Optional[float] = None
    motherboard: Optional[str] = None
    power_supply: Optional[str] = None


class OtherAttributes(BaseHardwareAttributes):
    category: Literal[HardwareCategory.OTHER] = HardwareCategory.OTHER
    raw_type: Optional[str] = None


HardwareAttributesUnion = Annotated[
    Union[
        NotebookAttributes,
        GPUAttributes,
        CPUAttributes,
        RAMAttributes,
        SSDAttributes,
        MotherboardAttributes,
        DesktopAttributes,
        OtherAttributes,
    ],
    Field(discriminator="category"),
]


class ClassificationResult(BaseModel):
    """Immutable result contract produced by the deterministic/hybrid classification service."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    category: HardwareCategory
    item_form: ItemForm
    status: ClassificationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    taxonomy_version: str = HARDWARE_TAXONOMY_VERSION
    schema_version: str = HARDWARE_SCHEMA_VERSION
    evidence: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    brand: str = ""
    model: str = ""
    variant: Optional[str] = None
    delivery_status: str = "UNKNOWN"
    delivery_confidence: float = 0.0
    delivery_evidence: List[str] = Field(default_factory=list)
    seller_verification: str = "UNKNOWN"
    seller_signal_level: str = "UNKNOWN"
    seller_evidence: List[str] = Field(default_factory=list)
    listing_summary: str = ""
    description_quality: str = "UNKNOWN"
    listing_risk_flags: List[str] = Field(default_factory=list)


class EligibilityEvaluationResult(BaseModel):
    """Pure analytical eligibility verdict for a classified listing fact."""

    analytics_eligible: bool
    exclusion_codes: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
