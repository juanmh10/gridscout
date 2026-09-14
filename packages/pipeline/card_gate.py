"""Cheap, auditable gates for search-card discovery.

The gates in this module intentionally run before the semantic agent and
before any listing-detail navigation.  They only use evidence displayed on a
search card, so a missing value is never upgraded to a confirmation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


CARD_GATE_CONTRACT_VERSION = "agent-card-gate-v1"
DIRECT_PRICE_ORIGINS = {"structured", "dom"}


@dataclass(frozen=True)
class GateDecision:
    status: str
    reason_code: str | None = None
    evidence: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.status == "agent_eligible"


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def direct_price_evidence(discovery: Any) -> tuple[float | None, str, str]:
    """Return normalized direct price, its origin and its visible raw value."""
    origin = _fold(getattr(discovery, "price_origin", ""))
    raw = str(
        getattr(discovery, "price_raw", "")
        or (getattr(discovery, "raw_summary", {}) or {}).get("price_raw", "")
        or (getattr(discovery, "raw_summary", {}) or {}).get("price_text", "")
        or ""
    ).strip()
    try:
        value = float(getattr(discovery, "price", None))
    except (TypeError, ValueError):
        value = None
    return value, origin, raw


def direct_price_gate(discovery: Any) -> GateDecision:
    """Validate direct card price without looking at the title or description."""
    value, origin, raw = direct_price_evidence(discovery)
    raw_folded = _fold(raw)
    if origin in {"title", "title_text", "inferred", "description"}:
        return GateDecision("rejected_no_price", "TITLE_PRICE_ONLY", ("Preço inferido apenas do texto.",))
    if origin not in DIRECT_PRICE_ORIGINS:
        return GateDecision("rejected_no_price", "NO_DIRECT_PRICE", ("Card sem preço direto visível.",))
    if any(marker in raw_folded for marker in ("a combinar", "consulte", "sob consulta", "troca", "permuta")):
        return GateDecision("rejected_no_price", "NO_DIRECT_PRICE", ("Card informa preço não negociável diretamente.",))
    if value is None:
        return GateDecision("rejected_no_price", "NO_DIRECT_PRICE", ("Card sem preço direto visível.",))
    if not math.isfinite(value) or value <= 0:
        return GateDecision("rejected_no_price", "INVALID_DIRECT_PRICE", ("Preço direto inválido.",))
    return GateDecision("agent_eligible", None, (f"Preço direto: {value:.2f} ({origin}).",))


def scope_min_price(discovery: Any) -> float | None:
    scope = getattr(discovery, "scope_run", None)
    snapshot = dict(getattr(scope, "scope_snapshot", {}) or {}) if scope else {}
    candidates: list[Any] = [snapshot.get("min_price")]
    intent = (snapshot.get("plan") or {}).get("intent") or {}
    budget = intent.get("budget") or {}
    candidates.extend([budget.get("min_price"), budget.get("min")])
    for value in candidates:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            return price
    return None


def scope_max_price(discovery: Any) -> float | None:
    scope = getattr(discovery, "scope_run", None)
    snapshot = dict(getattr(scope, "scope_snapshot", {}) or {}) if scope else {}
    candidates: list[Any] = [snapshot.get("max_price")]
    intent = (snapshot.get("plan") or {}).get("intent") or {}
    budget = intent.get("budget") or {}
    candidates.extend([budget.get("max_price"), budget.get("max")])
    for value in candidates:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            return price
    return None


def price_scope_gate(discovery: Any) -> GateDecision:
    direct = direct_price_gate(discovery)
    if not direct.approved:
        return direct
    value, _, _ = direct_price_evidence(discovery)
    minimum = scope_min_price(discovery)
    if minimum is not None and value is not None and value < minimum:
        return GateDecision(
            "rejected_scope",
            "PRICE_BELOW_SCOPE",
            (f"Preço direto de {value:.2f} abaixo do piso de {minimum:.2f}.",),
        )
    maximum = scope_max_price(discovery)
    if maximum is not None and value is not None and value > maximum:
        return GateDecision(
            "rejected_scope",
            "PRICE_ABOVE_SCOPE",
            (f"Preço direto de {value:.2f} acima do teto de {maximum:.2f}.",),
        )
    return direct


def _has_target_console(text: str) -> bool:
    return bool(re.search(r"\b(?:ps\s?[45]|playstation\s?[45]|xbox(?:\s+(?:series\s?[xs]|one))?|nintendo\s+switch)\b", text))


def deterministic_prefilter(discovery: Any) -> GateDecision:
    """Reject only unambiguous non-products; bundles remain candidates."""
    text = _fold(getattr(discovery, "title", ""))
    if not text:
        return GateDecision("withheld_unverified", "SCOPE_MISMATCH", ("Card sem título utilizável.",))

    # Buyer advertisements and services advertise the request/work, not a
    # target product.  Check these before console terms because their titles
    # commonly contain the desired model name.
    if re.match(r"^(?:compro|procuro|busco|quero\s+comprar|interessado\s+em|pago\s+(?:a\s+vista|no\s+pix|em\s+dinheiro)|troco\s+por|aceito\s+troca)\b", text) or re.search(r"\b(?:compro\s+seu|procuro\s+para\s+comprar)\b", text):
        return GateDecision("rejected_prefilter", "WANTED_BUYER_AD", ("Anúncio de procura/compra.",))
    if re.match(r"^(?:conserto|assistencia(?:\s+tecnica)?|manutencao|reparo|formatacao|limpeza\s+preventiva|reballing|destrave|desbloqueio)\b", text) or re.search(r"\b(?:assistencia\s+tecnica|servico\s+de\s+reparo)\b", text):
        return GateDecision("rejected_prefilter", "SERVICE_ONLY", ("Serviço anunciado, não produto.",))
    if re.match(r"^(?:jogo|game|fita|cartucho|midia\s+fisica|midia\s+digital)\b", text) or re.search(r"\b(?:jogo|game|fita|cartucho)\s+(?:para|de)\s+(?:ps[345]|playstation|xbox|nintendo|switch)\b", text):
        return GateDecision("rejected_prefilter", "GAME_ONLY", ("Jogo avulso anunciado.",))
    if (re.match(r"^(?:caixa|embalagem)\b", text) and any(token in text for token in ("vazia", "somente", "apenas", "sem o console", "sem aparelho"))) or re.search(r"\b(?:caixa\s+vazia|embalagem\s+vazia|apenas\s+a\s+caixa|somente\s+a\s+caixa)\b", text):
        return GateDecision("rejected_prefilter", "BOX_ONLY", ("Caixa ou embalagem vazia.",))
    if re.match(r"^(?:controle|joystick|gamepad|capa|cabo|carregador|headset|suporte|base|dock|skin|adesivo|case|bag|fone|cooler|ventoinha|fonte\s+avulsa)\b", text) or re.search(r"\b(?:apenas\s+o\s+controle|somente\s+o\s+controle|capa\s+de\s+silicone|suporte\s+de\s+parede)\b", text):
        return GateDecision("rejected_prefilter", "ACCESSORY_ONLY", ("Acessório avulso anunciado.",))
    if re.match(r"^(?:peca|placa\s+(?:mae|logica)|fonte|leitor|carcaca|sucata)\b", text) or re.search(r"\b(?:para\s+pecas|para\s+retirada|com\s+defeito|sucata|n[aã]o\s+liga|sem\s+video|placa\s+queimada|bloqueado|tela\s+(?:quebrada|trincada|preta)|iCloud\s+bloqueado|para\s+retirada\s+de\s+pe[cç]as)\b", text):
        return GateDecision("rejected_prefilter", "PARTS_ONLY", ("Peça, sucata ou componente avulso.",))

    scope = getattr(discovery, "scope_run", None)
    snapshot = dict(getattr(scope, "scope_snapshot", {}) or {}) if scope else {}
    expected = _fold(" ".join(str(value or "") for value in (snapshot.get("query"), snapshot.get("name"))))
    # These model families are mutually exclusive enough to reject only when
    # the saved scope explicitly asks for a console family.
    expected_ps5 = bool(re.search(r"\b(?:ps\s?5|playstation\s?5)\b", expected))
    if expected_ps5 and re.search(r"\b(?:ps\s?4|playstation\s?4|ps\s?portal|xbox|nintendo\s+switch)\b", text):
        return GateDecision("rejected_prefilter", "UNRELATED_PRODUCT", ("Produto claramente fora do modelo do escopo.",))
    return GateDecision("agent_eligible", None, ("Pré-filtro determinístico aprovado.",))


def cheap_gate(discovery: Any) -> GateDecision:
    price = price_scope_gate(discovery)
    if not price.approved:
        return price
    prefilter = deterministic_prefilter(discovery)
    if not prefilter.approved:
        return prefilter
    return GateDecision("agent_eligible", None, (*price.evidence, *prefilter.evidence))


def card_evidence_hash(discovery: Any) -> str:
    """Hash exactly the bounded card evidence supplied to the semantic gate."""
    price, origin, raw = direct_price_evidence(discovery)
    payload = {
        "contract": CARD_GATE_CONTRACT_VERSION,
        "title": str(getattr(discovery, "title", "") or ""),
        "price": price,
        "price_origin": origin,
        "price_raw": raw,
        "location": str(getattr(discovery, "location", "") or ""),
        "condition": str(getattr(discovery, "condition", "") or ""),
        "seller": str(getattr(discovery, "seller", "") or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_cheap_gate(discovery: Any) -> GateDecision:
    """Persist the cheap decision while preserving all raw discovery evidence."""
    decision = cheap_gate(discovery)
    discovery.gate_status = decision.status
    discovery.gate_reason_code = decision.reason_code
    discovery.gate_evidence = list(decision.evidence)
    discovery.gate_evidence_hash = card_evidence_hash(discovery)
    if decision.status.startswith("rejected_"):
        discovery.publication_status = decision.status
        discovery.publication_reason = decision.reason_code or "Card rejeitado pelo gate determinístico."
        discovery.triage_status = "rejected_summary"
        discovery.triage_reason = discovery.publication_reason
    return decision


def is_direct_price_valid(discovery: Any) -> bool:
    return direct_price_gate(discovery).approved
