"""Agent registry — maps agent names to their run functions."""
from . import (
    video_intelligence,
    object_action_analyst,
    temporal_reasoning,
    cross_camera_correlation,
    query_investigation,
    contradiction_hunter,
    evidence_auditor,
    report_synthesis,
)

AGENT_REGISTRY: dict[str, object] = {
    "VideoIntelligenceAgent": video_intelligence,
    "ObjectActionAnalystAgent": object_action_analyst,
    "TemporalReasoningAgent": temporal_reasoning,
    "CrossCameraCorrelationAgent": cross_camera_correlation,
    "QueryInvestigationAgent": query_investigation,
    "ContradictionHunterAgent": contradiction_hunter,
    "EvidenceAuditorAgent": evidence_auditor,
    "ReportSynthesisAgent": report_synthesis,
}

AGENT_ORDER = list(AGENT_REGISTRY.keys())
