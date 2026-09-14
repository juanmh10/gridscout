"""Auditable, conservative personalization signals for search ranking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class FeedbackSignal(BaseModel):
    candidate_id: str
    signal: str  # accepted, rejected, saved, clicked, hidden
    reason: str = ""
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    source: str = "user"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PersonalizationProfile(BaseModel):
    preferred_brands: list[str] = Field(default_factory=list)
    category_expertise: dict[str, float] = Field(default_factory=dict)
    signals: list[FeedbackSignal] = Field(default_factory=list)
    audit_reasons: list[str] = Field(default_factory=list)

    @property
    def signal_count(self) -> int:
        return len(self.signals)


def record_feedback(profile: PersonalizationProfile | dict | None, signal: FeedbackSignal | dict) -> PersonalizationProfile:
    current = PersonalizationProfile.model_validate(profile or {})
    event = FeedbackSignal.model_validate(signal)
    current.signals.append(event)
    if event.brand and event.signal in {"accepted", "saved", "clicked"} and event.brand not in current.preferred_brands:
        current.preferred_brands.append(event.brand)
    if event.category:
        delta = 0.2 if event.signal in {"accepted", "saved", "clicked"} else -0.1 if event.signal in {"rejected", "hidden"} else 0.0
        current.category_expertise[event.category] = round(max(-1.0, min(1.0, current.category_expertise.get(event.category, 0.0) + delta)), 4)
    reason = event.reason.strip() or f"signal={event.signal} candidate={event.candidate_id}"
    current.audit_reasons.append(reason)
    return current


def suggest_personalization(profile: PersonalizationProfile | dict | None) -> Optional[dict]:
    """Suggest a preference only after three auditable user signals."""

    current = PersonalizationProfile.model_validate(profile or {})
    if current.signal_count < 3:
        return None
    top_brand = current.preferred_brands[-1] if current.preferred_brands else None
    category = None
    if current.category_expertise:
        category = max(current.category_expertise.items(), key=lambda pair: pair[1])[0]
    if not top_brand and not category:
        return None
    return {
        "type": "preference_suggestion",
        "status": "suggested",
        "brand": top_brand,
        "category": category,
        "reason": "Sugestão baseada em pelo menos três sinais explícitos do usuário.",
        "signal_count": current.signal_count,
        "audit_reasons": current.audit_reasons[-3:],
    }

