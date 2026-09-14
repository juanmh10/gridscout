"""Versioned contracts for search intelligence.

These models are intentionally plain Pydantic contracts.  They are safe to
serialize in a pipeline scope snapshot and do not depend on SQLAlchemy models.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "search-intent-plan-v1"


class CriterionOperator(str, Enum):
    EQ = "eq"
    IN = "in"
    GTE = "gte"
    LTE = "lte"
    RANGE = "range"
    EQUIVALENT_OR_BETTER = "equivalent_or_better"


class ConditionV1(str, Enum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    ANY = "any"


class BudgetV1(BaseModel):
    """A monetary interval; either side may be omitted."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    min: Optional[float] = Field(default=None, ge=0)
    max: Optional[float] = Field(default=None, ge=0)
    currency: str = "BRL"

    @model_validator(mode="after")
    def validate_interval(self) -> "BudgetV1":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("budget.min must be less than or equal to budget.max")
        return self


class LocationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    country: str = "BR"
    state: Optional[str] = None
    city: Optional[str] = None
    radius_km: Optional[float] = Field(default=None, ge=0)


class SearchCriterionV1(BaseModel):
    """One typed, auditable constraint in a search plan.

    ``origin`` records why a constraint exists (for example ``user`` or
    ``taxonomy``), which is useful when inspecting generated plans.  Values are
    deliberately JSON-compatible because product attributes are marketplace
    dependent, while the operator itself is closed and validated.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field: str = Field(min_length=1)
    operator: CriterionOperator
    value: Any
    unit: Optional[str] = None
    weight: float = Field(default=1.0, ge=0, le=100)
    origin: str = Field(default="user", min_length=1)

    @field_validator("field", "origin")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("criterion text cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_operator_value(self) -> "SearchCriterionV1":
        if self.operator == CriterionOperator.IN:
            if not isinstance(self.value, (list, tuple, set)) or not self.value:
                raise ValueError("operator 'in' requires a non-empty list value")
        if self.operator == CriterionOperator.RANGE:
            valid_range = isinstance(self.value, (list, tuple)) and len(self.value) == 2
            valid_range = valid_range or (
                isinstance(self.value, dict)
                and {"min", "max"}.issubset(self.value)
            )
            if not valid_range:
                raise ValueError("operator 'range' requires [min, max] or {min, max}")
        return self


class SearchIntentV1(BaseModel):
    """Normalized user request, versioned independently of model providers."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_version: Literal["search-intent-v1"] = "search-intent-v1"
    category: Optional[str] = None
    brands: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    generations: list[str] = Field(default_factory=list)
    budget: Optional[BudgetV1] = None
    location: Optional[LocationV1 | str] = None
    condition: Optional[ConditionV1 | str] = None
    desired_count: int = Field(default=10, ge=1, le=100)
    raw_query: str = ""
    require_price: bool = True
    olx_pay_only: bool = False
    delivery_only: bool = False
    # Optional explicit constraints are accepted at intent time as well as in
    # the compiled plan.  This lets a session carry user-edited criteria across
    # turns while preserving one versioned wire contract.
    must: list[SearchCriterionV1] = Field(default_factory=list)
    should: list[SearchCriterionV1] = Field(default_factory=list)
    must_not: list[SearchCriterionV1] = Field(default_factory=list)

    @field_validator("budget", mode="before")
    @classmethod
    def normalize_budget(cls, value: Any) -> Any:
        if isinstance(value, (int, float)):
            return {"max": float(value)}
        if isinstance(value, dict):
            # Adapters sometimes use marketplace field names; normalize them
            # at the contract boundary rather than in every provider.
            return {
                "min": value.get("min", value.get("min_price")),
                "max": value.get("max", value.get("max_price")),
                "currency": value.get("currency", "BRL"),
            }
        return value

    @field_validator("brands", "families", "models", "generations")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            value = str(value).strip()
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                result.append(value)
        return result

    @field_validator("raw_query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return str(value or "").strip()


class SearchPlanV1(BaseModel):
    """Compiled search plan with hard/soft/exclusion criteria and query budget."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_version: Literal["search-plan-v1"] = "search-plan-v1"
    intent: SearchIntentV1
    must: list[SearchCriterionV1] = Field(default_factory=list)
    should: list[SearchCriterionV1] = Field(default_factory=list)
    must_not: list[SearchCriterionV1] = Field(default_factory=list)
    primary_queries: list[str] = Field(default_factory=list, max_length=3)
    fallback_queries: list[str] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)
    taxonomy_version: str = "hardware-taxonomy-v2"
    desired_count: int = Field(default=10, ge=1, le=100)
    max_results: int = Field(default=30, ge=1, le=30)
    scope_mode: Literal["precise", "broad"] = "precise"
    use_dataset_match: bool = True
    require_price: bool = True
    olx_pay_only: bool = False
    delivery_only: bool = False
    catalog_match: Optional[str] = None
    marketplace_review: list[dict[str, Any]] = Field(default_factory=list)
    native_filters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_plan(self) -> "SearchPlanV1":
        if self.desired_count != self.intent.desired_count:
            # Keep one source of truth when a provider emits both values.
            self.desired_count = self.intent.desired_count
        expected_max = min(self.desired_count * 3, 30)
        if self.max_results != expected_max:
            self.max_results = expected_max
        self.primary_queries = _unique_queries(self.primary_queries)
        self.fallback_queries = _unique_queries(self.fallback_queries)
        return self

    @property
    def criteria(self) -> dict[str, list[SearchCriterionV1]]:
        """Stable grouped view useful to API adapters and audit logs."""

        return {"must": self.must, "should": self.should, "must_not": self.must_not}

    @property
    def primary(self) -> list[str]:
        return self.primary_queries

    @property
    def fallback(self) -> list[str]:
        return self.fallback_queries

    def as_scope_queries(self) -> list[str]:
        return list(dict.fromkeys(self.primary_queries + self.fallback_queries))


def _unique_queries(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


class SearchCandidate(BaseModel):
    """A summary/detail candidate shared by strategy and ranking layers."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source: str = "fixture"
    external_id: str
    title: str
    price: Optional[float] = Field(default=None, ge=0)
    location: str = ""
    condition: str = ""
    url: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
