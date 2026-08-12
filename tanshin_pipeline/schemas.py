"""Pydantic schemas for selection, analysis, translation, and validation."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


NonEmpty = Annotated[str, Field(min_length=1)]
ModelProfile = Literal[
    "default",
    "key2-translation",
    "pro-translation",
    "pro",
    "sol",
]
ApiProvider = Literal["gemini", "openai"]
ProviderProfile = Literal["default", "key2-translation", "pro"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilingPeriod(str, Enum):
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    FY = "FY"
    UNKNOWN = "UNKNOWN"


class DiscoveredFiling(StrictModel):
    filename: NonEmpty
    relative_path: NonEmpty
    ordinal: int | None = Field(default=None, ge=1)
    fiscal_year: int = Field(ge=1900, le=2200)
    fiscal_month_hint: int | None = Field(default=None, ge=1, le=12)
    filing_date: str | None = None
    period: FilingPeriod
    period_explicit: bool
    year_end_inferred: bool = False
    classification_reason: NonEmpty
    page_count: int = Field(ge=1)
    byte_size: int = Field(ge=1)
    sha256: NonEmpty


class SelectedFiling(DiscoveredFiling):
    roles: list[Literal["latest", "trend_year_end"]] = Field(min_length=1)
    selection_reasons: list[NonEmpty] = Field(min_length=1)


class SelectionWindow(StrictModel):
    anchor_fiscal_year: int
    start_fiscal_year: int
    latest_year_end_fiscal_year: int
    unique_years: list[int]
    expected_unique_years: list[int]
    transition_years_with_multiple_year_ends: list[int]


class SelectionManifest(StrictModel):
    schema_version: NonEmpty
    security_code: NonEmpty
    data_directory: NonEmpty
    latest_filename: NonEmpty
    window: SelectionWindow
    selected_files: list[SelectedFiling] = Field(min_length=1)
    unselected_files: list[DiscoveredFiling]
    total_selected_pages: int = Field(ge=1)
    total_selected_bytes: int = Field(ge=1)
    selection_notes: list[str]
    manifest_id: NonEmpty


class StatementType(str, Enum):
    ACTUAL = "actual"
    FORECAST = "forecast"
    TARGET = "target"
    RISK = "risk"
    INFERENCE = "inference"
    MIXED = "mixed"


class DriverDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNCERTAIN = "uncertain"


class DriverNature(str, Enum):
    STRUCTURAL = "structural"
    CYCLICAL = "cyclical"
    COMPANY_SPECIFIC = "company_specific"
    MIXED = "mixed"


class CommitmentType(str, Enum):
    ANNUAL_FORECAST = "annual_forecast"
    MEDIUM_TERM_TARGET = "medium_term_target"
    STRATEGIC_COMMITMENT = "strategic_commitment"
    CAPITAL_ALLOCATION = "capital_allocation"


class CommitmentOutcome(str, Enum):
    ACHIEVED = "achieved"
    EXCEEDED = "exceeded"
    PARTLY_ACHIEVED = "partly_achieved"
    MISSED = "missed"
    DELAYED = "delayed"
    REVISED = "revised"
    WITHDRAWN = "withdrawn"
    PENDING = "pending"
    NOT_OBSERVABLE = "not_observable"


class RevisionDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    MIXED = "mixed"
    NONE_OBSERVED = "none_observed"
    NOT_ASSESSABLE = "not_assessable"


class ForecastPosture(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    MIXED = "mixed"
    NOT_ASSESSABLE = "not_assessable"


class ThemeDevelopment(str, Enum):
    PERSISTENT = "persistent"
    INTRODUCED = "introduced"
    STRENGTHENED = "strengthened"
    CHANGED = "changed"
    DEPRIORITIZED = "deprioritized"
    ABANDONED = "abandoned"


class ManagementConsistencyDimension(str, Enum):
    STRATEGIC_COHERENCE = "strategic_coherence"
    EXECUTION_FOLLOW_THROUGH = "execution_follow_through"
    FORECAST_TARGET_DISCIPLINE = "forecast_target_discipline"
    ACCOUNTABILITY_TRANSPARENCY = "accountability_transparency"


class ModelManagementConsistencyComponent(BaseModel):
    """One model-rated input to the locally calculated consistency score."""

    model_config = ConfigDict(extra="ignore")

    dimension: ManagementConsistencyDimension = Field(
        description="Exact consistency dimension being assessed."
    )
    rating: int | None = Field(
        ge=0,
        le=4,
        description=(
            "Evidence-based ordinal rating: 0 materially inconsistent, 1 weak, "
            "2 mixed, 3 generally consistent, or 4 highly consistent. Use null "
            "when longitudinal evidence is insufficient."
        ),
    )
    evidence_sufficiency: Literal["sufficient", "insufficient"] = Field(
        description=(
            "Whether the selected filings provide enough longitudinal evidence "
            "to assign this component a substantive rating."
        )
    )
    rationale_ja: NonEmpty = Field(
        description=(
            "Concise Japanese explanation comparing what management said with "
            "later actions, outcomes, revisions, or explanations. State both the "
            "strongest supporting evidence and any material counterevidence, and "
            "compare targets with results using the same scope and time horizon."
        )
    )
    evidence_ids: list[NonEmpty] = Field(
        description=(
            "Evidence IDs from management-discussion passages that support the "
            "rating or explain why the evidence is insufficient."
        ),
    )


class ModelManagementConsistency(BaseModel):
    """Model inputs only; Python calculates the published score."""

    model_config = ConfigDict(extra="ignore")

    components: list[ModelManagementConsistencyComponent] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Exactly one component for each of the four required consistency dimensions."
        ),
    )
    overall_rationale_ja: NonEmpty = Field(
        description=(
            "Concise synthesis of the company's management consistency across "
            "the selected trend period, including the strongest evidence for and "
            "against consistency and any important qualifications."
        )
    )


class ManagementConsistencyComponent(StrictModel):
    dimension: ManagementConsistencyDimension
    rating: int | None = Field(default=None, ge=0, le=4)
    evidence_sufficiency: Literal["sufficient", "insufficient"] = "sufficient"
    normalized_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Published 0-1 component score. After local calculation, this remains "
            "null when longitudinal evidence is insufficient; missing components "
            "are excluded from the overall arithmetic mean."
        ),
    )
    weight: float = Field(ge=0, le=1)
    rationale_ja: NonEmpty
    evidence_ids: list[NonEmpty]
    distinct_fiscal_years: list[int] = Field(default_factory=list)
    management_discussion_evidence_count: int = Field(default=0, ge=0)
    covered_period_buckets: list[
        Literal["early", "middle", "recent"]
    ] = Field(default_factory=list)
    evidence_confidence: float = Field(default=0, ge=0, le=1)


class ManagementConsistencyAssessment(StrictModel):
    methodology_version: NonEmpty
    score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Published 0-1 score calculated as the arithmetic mean of available "
            "component scores. Local calculation uses the neutral overall value "
            "0.50 only when every component is unavailable."
        ),
    )
    raw_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Unrounded arithmetic mean populated by local calculation.",
    )
    evidence_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_label: Literal["low", "moderate", "high"] | None = None
    # Backward-compatible alias retained in persisted artifacts.
    evidence_coverage: float | None = Field(default=None, ge=0, le=1)
    distinct_fiscal_years: list[int] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)
    management_discussion_evidence_share: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    components: list[ManagementConsistencyComponent]
    overall_rationale_ja: NonEmpty


class SectionKey(str, Enum):
    LATEST_KEY_TAKEAWAY = "latest.key_takeaway"
    LATEST_BUSINESS_DRIVER = "latest.business_driver"
    LATEST_OUTLOOK = "latest.outlook"
    LATEST_RISK = "latest.risk"
    LATEST_CONTEXT = "latest.context"
    TREND_PERSPECTIVE = "trend.perspective"
    TREND_CONSISTENT = "trend.consistent"
    TREND_CHANGE = "trend.change"
    TREND_CAPITAL_ALLOCATION = "trend.capital_allocation"
    TREND_IMPLICATION = "trend.implication"
    COMPANY_OVERVIEW = "company.overview"
    MANAGEMENT_STRATEGY = "management.strategy"
    MANAGEMENT_EXECUTION = "management.execution"
    MANAGEMENT_FORECAST_DISCIPLINE = "management.forecast_discipline"
    MANAGEMENT_ACCOUNTABILITY = "management.accountability"


class CompanyIdentity(StrictModel):
    security_code: NonEmpty = Field(
        description="Exact requested security code; do not infer a different issuer."
    )
    company_name_ja: NonEmpty = Field(
        description="Japanese company name stated in the latest supplied filing."
    )
    company_name_en: NonEmpty = Field(
        description=(
            "English company name stated in the supplied filings, or a faithful "
            "romanization when no English name is shown."
        )
    )
    latest_filename: NonEmpty = Field(
        description="Exact authoritative filename of the filing identified as latest."
    )
    latest_period_ja: NonEmpty = Field(
        description="Concise Japanese reporting-period label for the latest filing."
    )
    latest_period_en: NonEmpty = Field(
        description="Concise English equivalent of latest_period_ja."
    )


class SupportedSpan(StrictModel):
    value_id: NonEmpty
    claim_surface_ja: NonEmpty
    source_surface_ja: NonEmpty
    evidence_id: NonEmpty


class EvidenceRecord(StrictModel):
    evidence_id: NonEmpty = Field(
        description=(
            "Unique stable ID formatted as <source_filename>:sNNNN, using a "
            "four-digit sequence within the response."
        )
    )
    source_filename: NonEmpty = Field(
        description="Exact source_filename from the metadata preceding the cited PDF."
    )
    pdf_page: int = Field(
        ge=1,
        description=(
            "Physical 1-indexed PDF page containing the quote; never a printed "
            "footer, contents-page, or document page label."
        ),
    )
    exact_quote_ja: NonEmpty = Field(
        description=(
            "Verbatim contiguous Japanese sentence or short passage that contains "
            "the material facts, figures, dates, qualifiers, and causal wording used."
        )
    )
    period_label_ja: NonEmpty = Field(
        description="Concise Japanese period label suitable for a citation."
    )
    period_label_en: NonEmpty = Field(
        description="Concise English equivalent of period_label_ja."
    )
    statement_type: StatementType = Field(
        description="Nature of the quoted source statement, not the analyst's tone."
    )
    source_section: NonEmpty = Field(
        description="Concise filing section or table name where the quote appears."
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional short topical labels; use an empty list when unnecessary.",
    )


class ResearchBusinessDriver(BaseModel):
    """One comparable business driver extracted from the selected filings."""

    model_config = ConfigDict(extra="ignore")

    driver_id: NonEmpty
    canonical_tag: NonEmpty = Field(
        description=(
            "Short reusable driver category such as customer_demand, pricing, "
            "material_costs, labor_costs, interest_rates, foreign_exchange, "
            "utilization, regulation, technology_investment, or other."
        )
    )
    label_ja: NonEmpty = Field(
        description="Concise Japanese reader-facing driver label."
    )
    direction: DriverDirection
    importance: Literal["primary", "secondary"]
    nature: DriverNature
    affected_area_ja: NonEmpty = Field(
        description="Business, segment, margin, cash-flow, or balance-sheet area affected."
    )
    summary_ja: NonEmpty = Field(
        description=(
            "Concise explanation of the operating or financial transmission "
            "mechanism, including material qualifications."
        )
    )
    observed_periods_ja: list[NonEmpty] = Field(min_length=1)
    evidence_ids: list[NonEmpty] = Field(min_length=1)


class ResearchCommitmentRecord(BaseModel):
    """A forecast, target, or commitment connected to a later observable outcome."""

    model_config = ConfigDict(extra="ignore")

    commitment_id: NonEmpty
    commitment_type: CommitmentType
    statement_period_ja: NonEmpty
    commitment_ja: NonEmpty
    due_period_ja: NonEmpty
    outcome_status: CommitmentOutcome
    outcome_ja: NonEmpty = Field(
        description=(
            "Later result or a concise statement that the outcome is not yet "
            "observable in the selected filings."
        )
    )
    revision_direction: RevisionDirection
    forecast_posture: ForecastPosture
    evidence_ids: list[NonEmpty] = Field(
        min_length=1,
        description=(
            "Evidence for the original statement and, when observable, its "
            "revision or later outcome."
        ),
    )


class ResearchThemeRecord(BaseModel):
    """A recurring or changing management-commentary theme."""

    model_config = ConfigDict(extra="ignore")

    theme_id: NonEmpty
    label_ja: NonEmpty
    development: ThemeDevelopment
    early_period_ja: NonEmpty
    middle_period_ja: NonEmpty
    recent_period_ja: NonEmpty
    interpretation_ja: NonEmpty = Field(
        description=(
            "What remained consistent or changed, what management subsequently "
            "did, and what outcome or unresolved tension is visible."
        )
    )
    evidence_ids: list[NonEmpty] = Field(min_length=1)


class JapaneseResearchDossier(BaseModel):
    """PDF-backed research output consumed by the separate synthesis request."""

    model_config = ConfigDict(extra="ignore")

    schema_version: NonEmpty
    identity: CompanyIdentity
    evidence: list[EvidenceRecord] = Field(min_length=1)
    business_drivers: list[ResearchBusinessDriver] = Field(min_length=1)
    commitments: list[ResearchCommitmentRecord] = Field(default_factory=list)
    management_themes: list[ResearchThemeRecord] = Field(default_factory=list)
    management_consistency: ModelManagementConsistency
    research_notes: list[str] = Field(default_factory=list)


class AnalysisClaim(StrictModel):
    claim_id: NonEmpty
    section: SectionKey
    order: int = Field(ge=1)
    headline_ja: NonEmpty
    body_ja: NonEmpty
    evidence_ids: list[NonEmpty] = Field(min_length=1)
    statement_type: StatementType
    is_inference: bool = False
    causal: bool = False
    figures: list[SupportedSpan] = Field(default_factory=list)
    dates: list[SupportedSpan] = Field(default_factory=list)
    qualifiers: list[SupportedSpan] = Field(default_factory=list)


class ModelAnalysisClaim(BaseModel):
    """Quality-focused model response claim; support spans are derived locally."""

    model_config = ConfigDict(extra="ignore")

    claim_id: NonEmpty = Field(
        description="Unique stable claim ID, concise and descriptive within this report."
    )
    section: SectionKey = Field(
        description="Exact report section that this claim belongs to."
    )
    order: int = Field(
        ge=1,
        description="1-indexed display order within the claim's section.",
    )
    headline_ja: NonEmpty = Field(
        description=(
            "Concise analytical conclusion in Japanese, not a generic topic label "
            "or promotional slogan."
        )
    )
    body_ja: NonEmpty = Field(
        description=(
            "Evidence-grounded Japanese analysis explaining the conclusion, its "
            "period contrast, investor significance, material counterevidence or "
            "uncertainty, and the difference between management statements and "
            "subsequent outcomes without duplicating other claims or calculating "
            "unsupported values. A trend.consistent claim must explain a mechanism "
            "observed across early, middle, and recent periods and address a disclosed "
            "counterperiod. A trend.change claim must state the before, transition, "
            "and current condition."
        )
    )
    evidence_ids: list[NonEmpty] = Field(
        min_length=1,
        description=(
            "Unique evidence IDs from this response that collectively support every "
            "material factual assertion in the headline and body. For trend.consistent, "
            "cite separated year-end filings spanning early, middle, and recent period "
            "buckets. For trend.change, cite the before, transition, and current stages."
        ),
    )
    statement_type: StatementType = Field(
        description="Nature of the claim after accounting for all cited statements."
    )
    is_inference: bool = Field(
        default=False,
        description="True only when the claim contains analyst inference.",
    )
    causal: bool = Field(
        default=False,
        description="True when the claim asserts or infers a cause-and-effect relationship.",
    )


class JapaneseModelResponse(BaseModel):
    """Lightweight schema returned by Gemini before local normalization."""

    model_config = ConfigDict(extra="ignore")

    schema_version: NonEmpty = Field(
        description="Response-format version identifier; use one consistent value."
    )
    identity: CompanyIdentity = Field(
        description="Issuer and latest-report identity derived from the supplied PDFs."
    )
    claims: list[ModelAnalysisClaim] = Field(
        min_length=1,
        description=(
            "Grounded report claims ordered by section and then by the order field. "
            "Coverage targets may be underfilled rather than padded with weak claims."
        ),
    )
    evidence: list[EvidenceRecord] = Field(
        min_length=1,
        description=(
            "Deduplicated evidence ledger containing every evidence ID cited by a claim."
        ),
    )
    management_consistency: ModelManagementConsistency = Field(
        description=(
            "Four evidence-based management-consistency component ratings. "
            "Python calculates the published score from these inputs."
        ),
    )
    model_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Optional concise disclosure of source ambiguity, omitted material, or "
            "a coverage shortfall. Use coverage_shortfall:<section>:<reason> when "
            "the evidence does not support a requested claim count. Never use this "
            "field for report prose."
        ),
    )


class JapaneseSynthesisResponse(BaseModel):
    """Dossier-backed analytical prose returned by the second Japanese request."""

    model_config = ConfigDict(extra="ignore")

    schema_version: NonEmpty
    identity: CompanyIdentity
    claims: list[ModelAnalysisClaim] = Field(min_length=1)
    model_notes: list[str] = Field(default_factory=list)


class JapaneseAnalysis(StrictModel):
    schema_version: NonEmpty
    identity: CompanyIdentity
    claims: list[AnalysisClaim] = Field(min_length=1)
    evidence: list[EvidenceRecord] = Field(min_length=1)
    management_consistency: ManagementConsistencyAssessment | None = None
    model_notes: list[str] = Field(default_factory=list)


class TranslationInputIdentity(StrictModel):
    company_name_ja: NonEmpty = Field(
        description="Japanese issuer name supplied only as translation context."
    )
    company_name_en: NonEmpty = Field(
        description=(
            "Authoritative English issuer name already present in the Japanese "
            "analysis; reuse it rather than inventing or retranslating the name."
        )
    )
    latest_period_ja: NonEmpty = Field(
        description="Japanese latest-period label supplied as translation context."
    )
    latest_period_en: NonEmpty = Field(
        description=(
            "Authoritative English latest-period label already present in the "
            "Japanese analysis."
        )
    )


class TranslationInputSpan(StrictModel):
    value_id: NonEmpty = Field(
        description="Stable identifier that the translation patch must return."
    )
    claim_surface_ja: NonEmpty = Field(
        description="Exact Japanese claim surface that requires English rendering."
    )


class TranslationInputClaim(StrictModel):
    claim_id: NonEmpty = Field(
        description="Stable claim identifier that the translation patch must return."
    )
    section: SectionKey = Field(
        description=(
            "Report section supplied only as translation context; Python restores it "
            "in the final artifact."
        )
    )
    headline_ja: NonEmpty = Field(
        description="Complete Japanese headline to translate without condensation."
    )
    body_ja: NonEmpty = Field(
        description="Complete Japanese analytical body to translate without condensation."
    )
    figures: list[TranslationInputSpan] = Field(
        default_factory=list,
        description="Financial and numeric claim surfaces requiring English rendering.",
    )
    dates: list[TranslationInputSpan] = Field(
        default_factory=list,
        description="Date and period claim surfaces requiring English rendering.",
    )
    qualifiers: list[TranslationInputSpan] = Field(
        default_factory=list,
        description="Qualifier claim surfaces requiring English rendering.",
    )


class TranslationInput(StrictModel):
    identity_context: TranslationInputIdentity
    claims: list[TranslationInputClaim] = Field(min_length=1)


class TranslatedSpanPatch(StrictModel):
    value_id: NonEmpty = Field(
        description="Exact value_id from the corresponding translation input span."
    )
    claim_surface_en: NonEmpty = Field(
        description=(
            "English rendering of claim_surface_ja. Financial figures use "
            "consistent English yen notation while preserving currency, scale, "
            "sign, statement type, and economic value within display precision."
        )
    )


class TranslatedClaimPatch(StrictModel):
    claim_id: NonEmpty = Field(
        description="Exact claim_id from the corresponding translation input claim."
    )
    headline_en: NonEmpty = Field(
        description=(
            "Natural US investor-English translation of the complete Japanese "
            "headline, preserving its analytical strength and uncertainty."
        )
    )
    body_en: NonEmpty = Field(
        description=(
            "Complete, uncondensed US investor-English translation of the Japanese "
            "body with no new facts, conclusions, or promotional wording."
        )
    )
    figures: list[TranslatedSpanPatch] = Field(
        default_factory=list,
        description="One translated surface for every input figure value_id.",
    )
    dates: list[TranslatedSpanPatch] = Field(
        default_factory=list,
        description="One translated surface for every input date value_id.",
    )
    qualifiers: list[TranslatedSpanPatch] = Field(
        default_factory=list,
        description="One translated surface for every input qualifier value_id.",
    )


class EnglishTranslationPatch(StrictModel):
    claims: list[TranslatedClaimPatch] = Field(
        min_length=1,
        description="Exactly one translated patch for every input claim.",
    )
    model_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Optional concise translation ambiguities only; do not add report content."
        ),
    )


class TranslatedSpan(StrictModel):
    value_id: NonEmpty = Field(
        description="Exact value_id from the corresponding Japanese supported span."
    )
    claim_surface_en: NonEmpty = Field(
        description=(
            "Rendering surface used in the English claim. Financial figures use "
            "consistent English yen notation while preserving currency, scale, "
            "sign, statement type, and economic value within the stated display "
            "precision; dates and qualifiers may be translated while preserving "
            "meaning."
        )
    )
    source_surface_ja: NonEmpty = Field(
        description="Exact unchanged source_surface_ja from the Japanese analysis."
    )
    evidence_id: NonEmpty = Field(
        description="Exact unchanged evidence_id from the Japanese supported span."
    )


class TranslatedClaim(StrictModel):
    claim_id: NonEmpty = Field(
        description="Exact unchanged claim_id from the Japanese analysis."
    )
    section: SectionKey = Field(
        description="Exact unchanged section from the Japanese claim."
    )
    order: int = Field(
        ge=1,
        description="Exact unchanged order from the Japanese claim.",
    )
    headline_en: NonEmpty = Field(
        description=(
            "Natural US investor-English translation of the complete Japanese "
            "headline, preserving its analytical strength and uncertainty."
        )
    )
    body_en: NonEmpty = Field(
        description=(
            "Complete, uncondensed US investor-English translation of the Japanese "
            "body with no new facts, conclusions, or promotional wording."
        )
    )
    evidence_ids: list[NonEmpty] = Field(
        min_length=1,
        description="Exact unchanged evidence ID list from the Japanese claim.",
    )
    statement_type: StatementType = Field(
        description="Exact unchanged statement type from the Japanese claim."
    )
    is_inference: bool = Field(
        default=False,
        description="Exact unchanged inference flag from the Japanese claim.",
    )
    causal: bool = Field(
        default=False,
        description="Exact unchanged causal flag from the Japanese claim.",
    )
    figures: list[TranslatedSpan] = Field(
        default_factory=list,
        description=(
            "One span for every Japanese figure span, with matching IDs and the "
            "same economic value rendered in English yen notation."
        ),
    )
    dates: list[TranslatedSpan] = Field(
        default_factory=list,
        description="One translated span for every Japanese date span, with matching IDs.",
    )
    qualifiers: list[TranslatedSpan] = Field(
        default_factory=list,
        description=(
            "One translated span for every Japanese qualifier span, preserving "
            "degree and uncertainty."
        ),
    )


class EvidenceTranslation(StrictModel):
    evidence_id: NonEmpty = Field(
        description="Exact evidence_id of one Japanese evidence record."
    )
    quote_en: NonEmpty = Field(
        description=(
            "Legacy English evidence translation retained only for backward "
            "compatibility. New responses should omit evidence translations; "
            "rendering always uses exact_quote_ja."
        )
    )


class EnglishTranslation(StrictModel):
    schema_version: NonEmpty = Field(
        description="Exact unchanged schema_version from the Japanese analysis."
    )
    identity: CompanyIdentity = Field(
        description="Exact unchanged identity object from the Japanese analysis."
    )
    claims: list[TranslatedClaim] = Field(
        min_length=1,
        description=(
            "One translated claim for every Japanese claim, in the same section order."
        ),
    )
    evidence_translations: list[EvidenceTranslation] = Field(
        default_factory=list,
        description=(
            "Legacy compatibility field. New responses should omit this field or "
            "return an empty list because evidence remains in Japanese."
        ),
    )
    model_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Optional concise translation ambiguities only; do not add report content."
        ),
    )


class ValidationIssue(StrictModel):
    severity: Literal["error", "warning"]
    category: Literal["factual_integrity", "essential_quality", "diagnostic"] = (
        "diagnostic"
    )
    code: NonEmpty
    message: NonEmpty
    claim_id: str | None = None
    evidence_id: str | None = None


class ValidationResult(StrictModel):
    valid: bool
    publishable: bool
    factual_integrity_passed: bool
    quality_gate_passed: bool
    blocking_error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    language: Literal["ja", "en"]
    issues: list[ValidationIssue]
    statistics: dict[str, int | float | str | bool]


class RequestFileDescriptor(StrictModel):
    filename: NonEmpty
    relative_path: NonEmpty
    mime_type: Literal["application/pdf"]
    page_count: int = Field(ge=1)
    byte_size: int = Field(ge=1)
    sha256: NonEmpty


class RequestPlan(StrictModel):
    schema_version: NonEmpty
    stage: Literal["research", "analysis", "translation"]
    security_code: NonEmpty
    model_profile: ModelProfile = "default"
    provider: ApiProvider
    provider_profile: ProviderProfile | None = None
    model: NonEmpty
    request_options: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )
    request_id: NonEmpty
    manifest_id: NonEmpty
    system_prompt_sha256: NonEmpty
    prompt_sha256: NonEmpty
    response_schema_sha256: NonEmpty
    files: list[RequestFileDescriptor]
    style_blueprint_path: str | None = None
    style_blueprint_sha256: str | None = None
    exemplar_path: str | None = None
    exemplar_sha256: str | None = None
    max_output_tokens: int = Field(ge=1)
    makes_network_request: bool
    request_count_if_executed: Literal[1]


class CostStage(StrictModel):
    model: NonEmpty
    estimated_input_tokens: int = Field(ge=0)
    maximum_output_tokens: int = Field(ge=0)
    input_cost_usd: float = Field(ge=0)
    maximum_output_cost_usd: float = Field(ge=0)
    maximum_stage_cost_usd: float = Field(ge=0)
    input_cost_jpy: float = Field(ge=0)
    maximum_output_cost_jpy: float = Field(ge=0)
    maximum_stage_cost_jpy: float = Field(ge=0)


class CostEstimate(StrictModel):
    currency: Literal["USD"]
    display_currency: Literal["JPY"]
    usd_to_jpy_rate: float = Field(gt=0)
    pdf_tokens_per_page: int
    research: CostStage
    analysis: CostStage
    translation: CostStage
    maximum_one_pass_cost_usd: float
    maximum_configured_cost_usd: float
    maximum_one_pass_cost_jpy: float
    maximum_configured_cost_jpy: float
    maximum_api_attempts_per_stage: int
    assumptions: list[str]


class RunMetadata(StrictModel):
    schema_version: NonEmpty
    security_code: NonEmpty
    mode: Literal["dry-run", "research", "analysis", "translation", "reprocess"]
    model_profile: ModelProfile = "default"
    prepared_at_utc: NonEmpty
    repository_root: NonEmpty
    output_directory: NonEmpty
    manifest_id: NonEmpty
    analysis_model: NonEmpty
    translation_model: NonEmpty
    analysis_provider: ApiProvider = "gemini"
    translation_provider: ApiProvider = "gemini"
    api_requests_sent_by_this_invocation: int = Field(ge=0, le=1)
    environment_key_logged: Literal[False] = False


def materialize_japanese_analysis(
    response: JapaneseModelResponse | JapaneseAnalysis,
) -> JapaneseAnalysis:
    """Convert a lightweight model response into the richer internal schema."""

    if isinstance(response, JapaneseAnalysis):
        return response.model_copy(deep=True)
    assessment = None
    assessment = ManagementConsistencyAssessment(
        methodology_version="management-consistency-v1-pending",
        components=[
            ManagementConsistencyComponent(
                dimension=component.dimension,
                rating=component.rating,
                evidence_sufficiency=component.evidence_sufficiency,
                normalized_score=(
                    component.rating / 4
                    if component.rating is not None
                    else None
                ),
                weight=0,
                rationale_ja=component.rationale_ja,
                evidence_ids=list(component.evidence_ids),
            )
            for component in response.management_consistency.components
        ],
        overall_rationale_ja=(
            response.management_consistency.overall_rationale_ja
        ),
    )
    return JapaneseAnalysis(
        schema_version=response.schema_version,
        identity=response.identity.model_copy(deep=True),
        claims=[
            AnalysisClaim(
                **claim.model_dump(mode="python"),
                figures=[],
                dates=[],
                qualifiers=[],
            )
            for claim in response.claims
        ],
        evidence=[item.model_copy(deep=True) for item in response.evidence],
        management_consistency=assessment,
        model_notes=list(response.model_notes),
    )


def materialize_japanese_synthesis(
    dossier: JapaneseResearchDossier,
    response: JapaneseSynthesisResponse,
) -> JapaneseAnalysis:
    """Combine dossier evidence with synthesis prose into the internal schema."""

    if response.identity.model_dump(mode="json") != dossier.identity.model_dump(
        mode="json"
    ):
        raise ValueError(
            "The synthesis response changed the issuer or latest-filing identity."
        )
    evidence_ids = {item.evidence_id for item in dossier.evidence}
    unresolved = sorted(
        {
            evidence_id
            for claim in response.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in evidence_ids
        }
    )
    if unresolved:
        raise ValueError(
            "The synthesis response cited evidence IDs absent from the research "
            f"dossier: {', '.join(unresolved)}."
        )
    assessment = ManagementConsistencyAssessment(
        methodology_version="management-consistency-v1-pending",
        components=[
            ManagementConsistencyComponent(
                dimension=component.dimension,
                rating=component.rating,
                evidence_sufficiency=component.evidence_sufficiency,
                normalized_score=(
                    component.rating / 4
                    if component.rating is not None
                    else None
                ),
                weight=0,
                rationale_ja=component.rationale_ja,
                evidence_ids=list(component.evidence_ids),
            )
            for component in dossier.management_consistency.components
        ],
        overall_rationale_ja=dossier.management_consistency.overall_rationale_ja,
    )
    return JapaneseAnalysis(
        schema_version=response.schema_version,
        identity=response.identity.model_copy(deep=True),
        claims=[
            AnalysisClaim(
                **claim.model_dump(mode="python"),
                figures=[],
                dates=[],
                qualifiers=[],
            )
            for claim in response.claims
        ],
        evidence=[item.model_copy(deep=True) for item in dossier.evidence],
        management_consistency=assessment,
        model_notes=[
            *dossier.research_notes,
            *response.model_notes,
        ],
    )


def parse_japanese_analysis_payload(
    payload: dict[str, object],
) -> JapaneseModelResponse | JapaneseAnalysis:
    """Accept both legacy span-rich responses and the new lightweight response."""

    claims = payload.get("claims")
    if isinstance(claims, list) and any(
        isinstance(claim, dict)
        and any(key in claim for key in ("figures", "dates", "qualifiers"))
        for claim in claims
    ):
        return JapaneseAnalysis.model_validate(payload)
    if "management_consistency" not in payload:
        # Stored pre-v1.4 lightweight responses remain reprocessable offline,
        # but they cannot receive a semantic consistency score retroactively.
        legacy_payload = dict(payload)
        legacy_payload["claims"] = [
            {
                **claim,
                "figures": [],
                "dates": [],
                "qualifiers": [],
            }
            for claim in claims
            if isinstance(claim, dict)
        ]
        return JapaneseAnalysis.model_validate(legacy_payload)
    upgraded_payload = dict(payload)
    management = upgraded_payload.get("management_consistency")
    if isinstance(management, dict):
        components = management.get("components")
        if isinstance(components, list):
            upgraded_components = []
            for component in components:
                if not isinstance(component, dict):
                    upgraded_components.append(component)
                    continue
                upgraded_component = dict(component)
                if "evidence_sufficiency" not in upgraded_component:
                    upgraded_component["evidence_sufficiency"] = (
                        "sufficient"
                        if upgraded_component.get("rating") is not None
                        else "insufficient"
                    )
                upgraded_components.append(upgraded_component)
            upgraded_management = dict(management)
            upgraded_management["components"] = upgraded_components
            upgraded_payload["management_consistency"] = upgraded_management
    return JapaneseModelResponse.model_validate(upgraded_payload)
