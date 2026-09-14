"""Shared classification and normalization service for hardware marketplace listings.

Emits canonical HardwareCategory, ItemForm, ClassificationStatus, and typed attributes.
Shared by standard runner, high-volume pipeline, and local gateway fallback.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from packages.classification.scope_guard import pre_model_scope_guard
from packages.core.hardware_schemas import ClassificationResult
from packages.core.hardware_taxonomy import (
    HARDWARE_SCHEMA_VERSION,
    HARDWARE_TAXONOMY_VERSION,
    ClassificationStatus,
    HardwareCategory,
    ItemForm,
    normalize_category,
    normalize_item_form,
)


def _fold(text: Any) -> str:
    val = unicodedata.normalize("NFKD", str(text or ""))
    val = "".join(c for c in val if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", val).strip().casefold()


NOTEBOOK_PATTERNS = (
    r"\b(?:notebook|laptop|ultrabook|netbook|chromebook|macbook(?:\s*(?:air|pro))?)\b",
    r"\b(?:galaxy\s*book[0-9]?|book\s*e[0-9]{2}|book\s*pro|book\s*ultra)\b",
    r"\b(?:ideapad|thinkpad|legion|loq|thinkbook|yoga\s*(?:slim|[0-9])?)\b",
    r"\b(?:inspiron|vostro|latitude|alienware|xps\s*[0-9]{2}|dell\s*g[0-9]{2})\b",
    r"\b(?:nitro\s*[0-9v]*|aspire(?:\s*[0-9])?|predator|helios|triton|swift|spin)\b",
    r"\b(?:vivobook|zenbook|tuf\s*gaming|rog\s*strix|rog\s*zephyrus|rog\s*flow|expertbook)\b",
    r"\b(?:pavilion(?:\s*x360)?|omen|victus|elitebook|probook|envy|spectre|dragonfly|hp\s*25[0-9])\b",
    r"\b(?:positivo\s*(?:vision|motion|master)|vaio\s*f[eh][0-9]*|avell(?:\s*(?:ion|storm|titan|[ab][0-9]+))?)\b",
    r"\b(?:13\.3|14\.0|14|15\.6|16\.0|17\.3)\s*(?:\"|''|pol|polegadas)\b",
)


def extract_item_form(title: str, description: str = "") -> Tuple[ItemForm, List[str]]:
    """Determine the form of how the item is being sold."""
    text = _fold(f"{title} {description}")
    evidence: List[str] = []

    # 1. Parts / Defective
    parts_pat = r"\b(?:para\s+conserto|para\s+pe[cç]as|com\s+defeito|sucata|quebrad[oa]|n[aã]o\s+liga|n[aã]o\s+funciona|n[aã]o\s+d[aá]\s+video|artefatos?)\b"
    m_parts = re.search(parts_pat, text)
    if m_parts:
        evidence.append(f"Sinal de defeito/peças: '{m_parts.group(0)}'")
        return ItemForm.PARTS, evidence

    # 2. Bundles / Kits
    bundle_pat = r"\b(?:kit\s+upgrade|kit\s+gamer|combo|placa\s*[- ]?m[aã]e\s*\+\s*(?:proc|cpu|ram)|(?:ryzen|core\s*i[3579])\s*\+\s*placa)\b"
    m_bundle = re.search(bundle_pat, text)
    if m_bundle:
        evidence.append(f"Sinal de kit/combo: '{m_bundle.group(0)}'")
        return ItemForm.BUNDLE, evidence

    # 3. Full Systems / PCs
    full_sys_pat = r"\b(?:pc\s+gamer|computador\s+completo|setup\s+gamer|gabinete\s+(?:gamer|completo)|desktop\s+completo)\b"
    m_sys = re.search(full_sys_pat, text)
    if m_sys:
        evidence.append(f"Sinal de sistema completo: '{m_sys.group(0)}'")
        return ItemForm.FULL_SYSTEM, evidence

    # 4. Standalone component vs accessory
    is_part_accessory = bool(re.search(
        r"\b(?:fonte|carregador|teclado|carca[cç]a|tela\s+display|tampa|bateria|cooler|dobradi[cç]a|cabo\s+flat)\s+(?:para|de|p/)\s+(?:notebook|laptop|dell|acer|lenovo|samsung|asus|hp)\b",
        text
    ))
    if is_part_accessory and not any(k in text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook novo")):
        return ItemForm.ACCESSORY, ["Acessório/peça avulsa identificada"]

    # 5. Notebook identification
    is_ram_module_only = bool(re.search(r"\b(?:pente|m[oó]dulo|mem[oó]ria\s+ram|memoria\s+ram|sodimm)\b", text)) and bool(re.search(r"\b(?:para\s+notebook|p/\s*notebook|sodimm)\b", text)) and not any(k in text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp"))
    is_ssd_drive_only = bool(re.search(r"\b(?:ssd\s+nvme|ssd\s+sata|ssd\s+m\.2)\b", text)) and bool(re.search(r"\b(?:para\s+notebook|p/\s*notebook)\b", text)) and not any(k in text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp"))

    if not is_ram_module_only and not is_ssd_drive_only:
        if any(re.search(pat, text) for pat in NOTEBOOK_PATTERNS):
            return ItemForm.FULL_SYSTEM, ["Notebook completo identificado"]

    return ItemForm.STANDALONE, ["Item avulso padrão"]


def classify_listing_text(
    title: str,
    description: str = "",
    category_hint: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    listing_context: Optional[Dict[str, Any]] = None,
) -> ClassificationResult:
    """Classify listing deterministically from title, description and context."""
    full_text = _fold(f"{title} {description}")
    title_folded = _fold(title)
    attrs = dict(attributes or {})
    evidence: List[str] = []

    # 1. Determine Item Form
    item_form, form_evidence = extract_item_form(title, description)
    evidence.extend(form_evidence)

    # 2. Classify Category
    detected_cat: HardwareCategory = HardwareCategory.OTHER
    brand: str = ""
    model: str = ""
    variant: Optional[str] = None
    confidence = 0.50
    extracted_attrs: Dict[str, Any] = {}

    # Check standalone RAM/SSD accessory exception
    is_standalone_ram_for_nb = bool(re.search(r"\b(?:pente|m[oó]dulo|mem[oó]ria\s+ram|memoria\s+ram|sodimm)\b", full_text)) and bool(re.search(r"\b(?:para\s+notebook|p/\s*notebook|sodimm)\b", full_text)) and not any(k in full_text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp"))
    is_standalone_ssd_for_nb = bool(re.search(r"\b(?:ssd\s+nvme|ssd\s+sata|ssd\s+m\.2)\b", full_text)) and bool(re.search(r"\b(?:para\s+notebook|p/\s*notebook)\b", full_text)) and not any(k in full_text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp"))

    # Notebook Complete check
    is_notebook_complete = (
        any(re.search(pat, full_text) for pat in NOTEBOOK_PATTERNS)
        and not is_standalone_ram_for_nb
        and not is_standalone_ssd_for_nb
    )

    # Check Motherboard
    is_motherboard = bool(re.search(r"\b(?:placa\s*[- ]?m[aã]e|motherboard|mainboard)\b", full_text)) and not bool(re.search(r"\bkit\b", full_text)) and not is_notebook_complete

    # Check GPU (Must not be notebook or complete desktop)
    is_gpu = (
        bool(re.search(r"\b(?:rtx\s*[0-9]{4}|gtx\s*[0-9]{3,4}|radeon\s+rx\s*[0-9]{4}|rx\s*[0-9]{4}\s*(?:xt|xtx)?|placa\s+de\s+v[ií]deo|placa\s+de\s+video|gpu|arc\s+a[0-9]{3})\b", full_text))
        and not is_notebook_complete
        and item_form != ItemForm.FULL_SYSTEM
    )

    # Check CPU (Must not be notebook, motherboard, or desktop)
    is_cpu = (
        bool(re.search(r"\b(?:processador|ryzen\s+[3579]|core\s+i[3579]|core\s+ultra\s+[579]|intel\s+core|xeon|epyc)\b", full_text))
        and not is_notebook_complete
        and not is_motherboard
        and item_form != ItemForm.FULL_SYSTEM
    )

    # Check RAM (Must be standalone memory module, not a whole notebook/PC)
    has_ram_signal = bool(re.search(r"\b(?:mem[oó]ria\s+ram|memoria\s+ram|pente\s+de\s+mem[oó]ria|pente\s+de\s+memoria|pente\s+ram|mem[oó]ria\s+sodimm|memoria\s+sodimm|mem[oó]ria\s+udimm|memoria\s+udimm|m[oó]dulo\s+de\s+mem[oó]ria|sodimm|rdimm|ddr[345]\s+[0-9]+(?:gb|mhz))\b", full_text)) or is_standalone_ram_for_nb
    is_ram = has_ram_signal and not is_notebook_complete and item_form != ItemForm.FULL_SYSTEM and not is_cpu and not is_gpu

    # Check SSD (Must be standalone storage drive, not a whole notebook/PC)
    has_ssd_signal = bool(re.search(r"\b(?:ssd\s+nvme|ssd\s+sata|ssd\s+m\.2|ssd\s+pcie|disco\s+ssd|unidade\s+ssd|ssd\s+(?:kingston|sandisk|crucial|samsung|adata|xpg|wd))\b", full_text)) or is_standalone_ssd_for_nb
    is_ssd = has_ssd_signal and not is_notebook_complete and item_form != ItemForm.FULL_SYSTEM and not is_gpu and not is_cpu

    # Check Desktop
    is_desktop = item_form == ItemForm.FULL_SYSTEM and not is_notebook_complete

    # Check Console
    is_console = bool(re.search(r"\b(?:ps[345]|playstation|xbox|nintendo\s+switch|steam\s+deck|rog\s+ally)\b", full_text)) and item_form != ItemForm.PARTS and not is_notebook_complete

    # Check Peripheral
    is_peripheral = bool(re.search(r"\b(?:teclado|mouse|headset|fone|monitor|controle|joystick|volante)\b", full_text)) and item_form != ItemForm.FULL_SYSTEM and not is_notebook_complete

    if is_notebook_complete:
        detected_cat = HardwareCategory.NOTEBOOK
        item_form = ItemForm.FULL_SYSTEM
        # Brand detection
        for b in ("Apple", "Samsung", "Lenovo", "Dell", "Acer", "ASUS", "HP", "Avell", "VAIO", "Positivo"):
            if _fold(b) in full_text:
                brand = b
                break
        # Model detection
        m_model = re.search(r"\b(MacBook\s+(?:Air|Pro)\s+[A-Za-z0-9\s]+|Galaxy\s*Book\s*[1-4]?(?:\s+Pro|\s+Ultra)?|G15\s*[0-9]{4}|Nitro\s*(?:V\s*15|5)?|Ideapad\s*[A-Za-z0-9]+|ThinkPad\s*[A-Za-z0-9]+|Vivobook\s*[A-Za-z0-9]+|Inspiron\s*[A-Za-z0-9]+|TUF\s*Gaming\s*[A-Za-z0-9]+)\b", title, re.IGNORECASE)
        if m_model:
            model = m_model.group(0).strip()
        else:
            model = title[:40].strip()

        # Specs
        m_ram = re.search(r"\b(\d+)\s*(?:gb|g)\s*(?:ram|mem[oó]ria)?\b", full_text)
        if m_ram:
            try:
                extracted_attrs["ram_gb"] = float(m_ram.group(1))
            except ValueError:
                pass
        if "ips" in full_text or "wva" in full_text:
            extracted_attrs["screen_ips"] = True
            extracted_attrs["panel_type"] = "IPS"
        confidence = 0.90 if brand and model else 0.70
        evidence.append(f"Notebook identificado: brand='{brand}', model='{model}'")

    elif is_gpu:
        detected_cat = HardwareCategory.GPU
        # Chip model
        m_rtx = re.search(r"\b(RTX\s*[0-9]{4}(?:\s*Ti|\s*Super)?|GTX\s*[0-9]{3,4}(?:\s*Ti|\s*Super)?|RX\s*[0-9]{4}(?:\s*XT|\s*XTX)?|Arc\s+A[0-9]{3})\b", full_text, re.IGNORECASE)
        if m_rtx:
            chip_model = m_rtx.group(0).upper().replace("  ", " ")
            model = chip_model
            extracted_attrs["chip_model"] = chip_model
            extracted_attrs["chipset"] = chip_model
            if "rtx" in _fold(chip_model) or "gtx" in _fold(chip_model):
                extracted_attrs["chip_brand"] = "NVIDIA"
                brand = "NVIDIA"
            elif "rx" in _fold(chip_model) or "radeon" in _fold(chip_model):
                extracted_attrs["chip_brand"] = "AMD"
                brand = "AMD"
            elif "arc" in _fold(chip_model):
                extracted_attrs["chip_brand"] = "Intel"
                brand = "Intel"
        # VRAM
        m_vram = re.search(r"\b(\d{1,2})\s*(?:gb|g)\b", full_text)
        if m_vram:
            try:
                vram_val = float(m_vram.group(1))
                if vram_val in (4, 6, 8, 10, 12, 16, 20, 24):
                    variant = f"{int(vram_val)}GB"
                    extracted_attrs["vram_gb"] = vram_val
            except ValueError:
                pass
        # AIB Partner
        for aib in ("ASUS", "MSI", "Gigabyte", "Galax", "EVGA", "Zotac", "PowerColor", "Sapphire", "XFX", "Gainward", "Palit", "PNY"):
            if _fold(aib) in full_text:
                extracted_attrs["aib_partner"] = aib
                break
        confidence = 0.95 if model else 0.70
        evidence.append(f"GPU identificada: model='{model}', vram='{variant or 'N/A'}'")

    elif is_cpu:
        detected_cat = HardwareCategory.CPU
        m_ryzen = re.search(r"\b(Ryzen\s+[3579]\s+[0-9]{4}[A-Za-z0-9]*)\b", title, re.IGNORECASE) or re.search(r"\b(Ryzen\s+[3579]\s+[0-9]{4}[A-Za-z0-9]*)\b", full_text, re.IGNORECASE)
        m_core = re.search(r"\b(Core\s+i[3579][- ][0-9]{4,5}[A-Za-z0-9]*|i[3579][- ][0-9]{4,5}[A-Za-z0-9]*)\b", title, re.IGNORECASE) or re.search(r"\b(Core\s+i[3579][- ][0-9]{4,5}[A-Za-z0-9]*|i[3579][- ][0-9]{4,5}[A-Za-z0-9]*)\b", full_text, re.IGNORECASE)
        if m_ryzen:
            brand = "AMD"
            model = m_ryzen.group(0).replace("  ", " ").strip()
            extracted_attrs["brand"] = "AMD"
            extracted_attrs["model_number"] = model
            extracted_attrs["model"] = model
            if "5600x" in _fold(model) or "5600" in _fold(model) or "5800x" in _fold(model):
                extracted_attrs["socket"] = "AM4"
            elif "7800x3d" in _fold(model):
                extracted_attrs["socket"] = "AM5"
        elif m_core:
            brand = "Intel"
            model = m_core.group(0).replace("  ", " ").strip()
            extracted_attrs["brand"] = "Intel"
            extracted_attrs["model_number"] = model
            extracted_attrs["model"] = model
            if "12400" in model or "13700" in model:
                extracted_attrs["socket"] = "LGA1700"
        else:
            model = title[:30].strip()
            extracted_attrs["model"] = model
        confidence = 0.95 if brand and model else 0.65
        evidence.append(f"CPU identificada: brand='{brand}', model='{model}'")

    elif is_ram:
        detected_cat = HardwareCategory.RAM
        # Form factor
        if "sodimm" in full_text or is_ram_for_nb:
            extracted_attrs["form_factor"] = "SODIMM"
        elif "rdimm" in full_text or "ecc reg" in full_text:
            extracted_attrs["form_factor"] = "RDIMM"
            extracted_attrs["ecc_support"] = True
        else:
            extracted_attrs["form_factor"] = "UDIMM"

        # DDR standard
        m_ddr = re.search(r"\b(DDR[345])\b", full_text, re.IGNORECASE)
        if m_ddr:
            ddr_std = m_ddr.group(0).upper()
            extracted_attrs["ddr_standard"] = ddr_std
            extracted_attrs["ram_generation"] = ddr_std

        # Capacity
        m_cap = re.search(r"\b(\d{1,3})\s*(?:gb|g)\b", full_text)
        if m_cap:
            try:
                cap_val = float(m_cap.group(1))
                if cap_val in (4, 8, 16, 32, 64, 128):
                    extracted_attrs["total_capacity_gb"] = cap_val
                    extracted_attrs["capacity_gb"] = cap_val
            except ValueError:
                pass

        # Frequency
        m_freq = re.search(r"\b(\d{4})\s*(?:mhz|mts|mt/s)\b", full_text, re.IGNORECASE)
        if m_freq:
            try:
                freq_val = float(m_freq.group(1))
                extracted_attrs["frequency_mhz"] = freq_val
                extracted_attrs["speed_mhz"] = freq_val
            except ValueError:
                pass

        # Brand
        for b in ("Kingston", "Corsair", "XPG", "G.Skill", "Crucial", "TeamGroup", "Asgard", "Juhor"):
            if _fold(b) in full_text:
                brand = b
                break

        model = f"{extracted_attrs.get('ddr_standard', 'RAM')} {int(extracted_attrs.get('total_capacity_gb', 0))}GB {extracted_attrs.get('form_factor', '')}".strip()
        confidence = 0.90 if extracted_attrs.get("ddr_standard") and extracted_attrs.get("total_capacity_gb") else 0.60
        evidence.append(f"RAM identificada: {model}")

    elif is_ssd:
        detected_cat = HardwareCategory.SSD
        model = "SSD"
        confidence = 0.75

    elif is_motherboard:
        detected_cat = HardwareCategory.MOTHERBOARD
        model = "Placa-Mãe"
        confidence = 0.75

    elif is_desktop:
        detected_cat = HardwareCategory.DESKTOP
        model = "Desktop Completo"
        confidence = 0.75

    elif is_console:
        detected_cat = HardwareCategory.CONSOLE
        for b in ("Sony", "Microsoft", "Nintendo", "Valve", "ASUS"):
            if _fold(b) in full_text:
                brand = b
                break

        m_console = re.search(
            r"\b(PS5(?:\s+(?:Slim|Pro|Digital|Fat))?|PlayStation\s*5(?:\s+(?:Slim|Pro|Digital|Fat))?|"
            r"PS4(?:\s+(?:Pro|Slim|Fat))?|PlayStation\s*4(?:\s+(?:Pro|Slim|Fat))?|"
            r"Xbox\s*Series\s*[XS]|Xbox\s*One(?:\s*[SX])?|"
            r"Nintendo\s*Switch(?:\s+(?:OLED|Lite|V2))?|"
            r"Steam\s*Deck|ROG\s*Ally)\b",
            title,
            re.IGNORECASE,
        )
        if m_console:
            model = m_console.group(0).replace("  ", " ").strip()
            if "PS" in model.upper() or "PLAYSTATION" in model.upper():
                brand = "Sony"
            elif "XBOX" in model.upper():
                brand = "Microsoft"
            elif "NINTENDO" in model.upper() or "SWITCH" in model.upper():
                brand = "Nintendo"
            elif "STEAM DECK" in model.upper():
                brand = "Valve"
            elif "ROG ALLY" in model.upper():
                brand = "ASUS"
            confidence = 0.90
            evidence.append(f"Console/Gadget identificado: brand='{brand}', model='{model}'")
        else:
            model = title[:40].strip()
            confidence = 0.50
            
    elif is_peripheral:
        detected_cat = HardwareCategory.PERIPHERAL
        for b in ("Logitech", "Razer", "Corsair", "HyperX", "Redragon", "Fallen", "Pichau", "Mancer"):
            if _fold(b) in full_text:
                brand = b
                break
        model = title[:40].strip()
        confidence = 0.70
        evidence.append(f"Periférico identificado: brand='{brand}', model='{model}'")

    else:
        # Fallback to category hint if provided
        if category_hint:
            detected_cat = normalize_category(category_hint)
        else:
            evidence.append("Não se enquadra nas categorias de hardware cobertas; classificado como Outro.")
            detected_cat = HardwareCategory.OTHER

    # 3. Status confirmation
    if model and confidence >= 0.70:
        status = ClassificationStatus.CONFIRMED
    else:
        status = ClassificationStatus.UNVERIFIED

    # 4. Triage / Context fields
    from packages.ai.model_gateway import _triage_fields
    triage = _triage_fields(title, description, listing_context)

    return ClassificationResult(
        category=detected_cat,
        item_form=item_form,
        status=status,
        confidence=round(confidence, 2),
        taxonomy_version=HARDWARE_TAXONOMY_VERSION,
        schema_version=HARDWARE_SCHEMA_VERSION,
        evidence=evidence,
        attributes=extracted_attrs,
        brand=brand or "Generic",
        model=model or title[:30],
        variant=variant,
        delivery_status=triage.get("delivery_status", "UNKNOWN"),
        delivery_confidence=triage.get("delivery_confidence", 0.0),
        delivery_evidence=triage.get("delivery_evidence", []),
        seller_verification=triage.get("seller_verification", "UNKNOWN"),
        seller_signal_level=triage.get("seller_signal_level", "UNKNOWN"),
        seller_evidence=triage.get("seller_evidence", []),
        listing_summary=triage.get("listing_summary", f"{title.strip()}. {description[:200].strip()}"),
        description_quality=triage.get("description_quality", "COMPLETE" if len(description) >= 24 else "INCOMPLETE"),
        listing_risk_flags=triage.get("listing_risk_flags", []),
    )
