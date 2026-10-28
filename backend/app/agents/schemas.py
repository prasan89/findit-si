"""Pydantic output schemas for all 8 investigation agents."""
from typing import Optional
from pydantic import BaseModel


class VisualSummary(BaseModel):
    """Agent 1: VideoIntelligenceAgent output."""
    event_id: str
    visual_notes: list[str]
    frame_references: list[str]


class VisualSummaryReport(BaseModel):
    summaries: list[VisualSummary]
    events_analyzed: int
    model_used: str


class ObjectActionReport(BaseModel):
    """Agent 2: ObjectActionAnalystAgent output."""
    event_id: str
    objects_identified: list[str]
    actions_identified: list[str]
    scene_change_detected: bool


class ObjectActionAnalysis(BaseModel):
    reports: list[ObjectActionReport]
    events_analyzed: int
    model_used: str


class TemporalEntry(BaseModel):
    event_id: str
    recording_id: str
    abs_timestamp: Optional[float]
    relative_order: int
    video_timestamp_seconds: float


class TemporalReport(BaseModel):
    """Agent 3: TemporalReasoningAgent output."""
    timeline: list[TemporalEntry]
    timestamp_reliable: bool
    gaps_detected: list[str]
    inconsistencies: list[str]


class CorrelationLink(BaseModel):
    source_event_id: str
    target_event_id: str
    confidence: float
    explanation: str


class CorrelationReport(BaseModel):
    """Agent 4: CrossCameraCorrelationAgent output."""
    candidate_links: list[CorrelationLink]
    confirmed_links: list[CorrelationLink]
    total_links_found: int
    skipped_reason: Optional[str] = None


class RankedEvent(BaseModel):
    event_id: str
    recording_id: str
    score: float
    timestamp_seconds: float
    action_description: str


class EvidenceBundle(BaseModel):
    """Agent 5: QueryInvestigationAgent output."""
    query: str
    ranked_events: list[RankedEvent]
    narrative_draft: str
    evidence_gaps: list[str]
    total_searched: int


class ContradictionItem(BaseModel):
    description: str
    event_ids: list[str]
    severity: str  # "high" | "medium" | "low"


class ContradictionReport(BaseModel):
    """Agent 6: ContradictionHunterAgent output."""
    contradictions_found: bool
    items: list[ContradictionItem]
    alternative_explanations: list[str]
    evidence_gaps: list[str]
    model_used: str


class RejectedClaim(BaseModel):
    claim: str
    reason: str
    event_id: str


class AuditReport(BaseModel):
    """Agent 7: EvidenceAuditorAgent output."""
    claims_verified: int
    claims_rejected: int
    rejected_items: list[RejectedClaim]
    verified_events: list[str]
    model_used: str


class ClaimEvidence(BaseModel):
    """Phase 5: minimal claim-to-evidence provenance for one report claim."""
    claim: str
    event_ids: list[str] = []
    evidence_refs: list[str] = []
    verified: Optional[bool] = None


class FinalReport(BaseModel):
    """Agent 8: ReportSynthesisAgent output."""
    narrative: str
    key_findings: list[str]
    uncertainty_notes: list[str]
    evidence_citations: list[str]
    model_used: str
    agents_run: list[str]
    # Phase 5: which claims were included and what evidence supports each.
    claim_evidence: list[ClaimEvidence] = []
