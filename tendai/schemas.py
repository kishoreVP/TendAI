"""Versioned, validated data contracts. Unknown values stay None - never invented."""
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Category = Literal[
    "administrative", "eligibility", "legal_registration", "financial", "emd_bid_security",
    "technical", "quality_standards", "experience", "delivery", "warranty", "contractual",
    "documents", "commercial", "other",
]
Operator = Literal["<=", ">=", "<", ">", "==", "approx", "matches", "in", "provided", "document"]
Status = Literal["MEETS_REQUIREMENT", "DOES_NOT_MEET_REQUIREMENT", "INSUFFICIENT_EVIDENCE",
                 "REQUIRES_HUMAN_REVIEW", "NOT_APPLICABLE"]
EvidenceType = Literal["bidder_declaration", "third_party_document", "external_verified",
                       "ai_inferred", "unknown"]


class Region(BaseModel):
    page: int | None = None
    bbox: list[float] | None = None
    sheet: str | None = None
    row: int | None = None
    col: int | None = None
    paragraph: int | None = None


class Rule(BaseModel):
    """What the deterministic evaluator executes."""
    op: Operator
    value: float | str | None = None
    unit: str | None = None
    permitted_values: list[str] | None = None
    # documentary checks: heading/keywords to find, attributes that must be present (e.g. "UDIN")
    document_keywords: list[str] | None = None
    must_mention: list[str] | None = None
    # conditional exemption: if evidence matching these keywords exists, the requirement needs review
    exemption_keywords: list[str] | None = None
    # bidder passages must match this regex (with their section path) to count as evidence
    evidence_pattern: str | None = None
    # "max": the best single value counts (e.g. turnover in any one year); None: all must agree
    aggregate: str | None = None
    version: str = "1"


class Requirement(BaseModel):
    schema_version: str = SCHEMA_VERSION
    requirement_id: str  # stable key within the case
    tender_id: str
    category: Category
    subcategory: str | None = None
    title: str
    original_clause_text: str  # verbatim source text, never edited
    normalised_requirement: str
    requirement_type: Literal["numeric", "boolean", "categorical", "text", "documentary", "conditional"]
    mandatory_status: Literal["mandatory", "desirable", "conditional", "informational"]
    applicability_conditions: str | None = None
    expected_value: str | None = None
    minimum_value: float | None = None
    maximum_value: float | None = None
    unit: str | None = None
    permitted_values: list[str] | None = None
    comparison_operator: Operator
    evaluation_rule: Rule
    required_evidence_types: list[str] = Field(default_factory=list)
    source_document_id: int
    source_node_id: int | None = None
    source_page: int | None = None
    source_clause: str | None = None
    source_regions: list[Region] = Field(default_factory=list)
    extraction_method: str  # e.g. "rule:gtp_table", "rule:turnover", "llm:groq/openai/gpt-oss-120b"
    extraction_confidence: float
    flags: list[str] = Field(default_factory=list)  # ambiguity, duplicate-of, conflict
    version: int = 1


class Claim(BaseModel):
    """One piece of bidder evidence for one requirement."""
    bidder: str
    requirement_id: str
    claim_text: str  # verbatim
    value: float | str | None = None  # normalised to the requirement's unit when numeric
    unit: str | None = None
    raw_value: str | None = None
    evidence_type: EvidenceType
    source_document_id: int
    source_node_id: int | None = None
    source_page: int | None = None
    source_label: str | None = None  # e.g. section heading
    source_regions: list[Region] = Field(default_factory=list)
    extraction_method: str
    extraction_confidence: float
    verification_status: Literal["unverified", "verified", "ai_extracted"] = "unverified"


class BenchmarkRecord(BaseModel):
    code: str | None = None
    original_description: str
    normalised_description: str
    attributes: dict = Field(default_factory=dict)  # parsed technical attributes
    unit: str
    rate: float
    currency: str = "INR"
    tax_basis: str | None = None
    effective_date: str | None = None
    geography: str | None = None
    issuer: str | None = None
    source_type: Literal["official_sor", "historical_award", "supplier_quotation",
                         "market_estimate", "SYNTHETIC_TEST"]
    source_page: int | None = None
    source_regions: list[Region] = Field(default_factory=list)
    source_url: str | None = None
    extraction_confidence: float
    verification_status: str = "unverified"
