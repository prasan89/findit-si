"""Base types for the multi-agent investigation system."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    # Phase 5: agent produced heuristic/fallback output without a live model
    # call (e.g. no API key configured). Output shape is unchanged.
    MOCKED = "mocked"
    # Phase 6: agent exceeded its execution timeout and was cancelled.
    TIMED_OUT = "timed_out"


@dataclass
class AgentResult(Generic[T]):
    agent_name: str
    status: AgentStatus
    output: Optional[T]
    duration_ms: float
    error: Optional[str] = None
    tokens_used: int = 0
    cost_usd: float = 0.0
    # Phase 5 observability (all optional; None == unavailable, never invented).
    retries: Optional[int] = None
    timeouts: Optional[int] = None
    cache_hits: Optional[int] = None
    cache_misses: Optional[int] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    # True only when tokens/cost came from a provider-reported usage payload.
    usage_measured: bool = False
    model_used: Optional[str] = None
    provider_used: Optional[str] = None
    event_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        output_val = None
        if self.output is not None:
            if hasattr(self.output, "model_dump"):
                output_val = self.output.model_dump()
            else:
                output_val = self.output
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "output": output_val,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
            "retries": self.retries,
            "timeouts": self.timeouts,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usage_measured": self.usage_measured,
            "model_used": self.model_used,
            "provider_used": self.provider_used,
            "event_ids": self.event_ids,
            "evidence_ids": self.evidence_ids,
        }


@dataclass
class InvestigationContext:
    """Shared context passed between agents."""
    query: str
    db_path: str
    recording_ids: list[str]
    budget_usd: float
    budget_spent: float = 0.0
    events: list[dict] = field(default_factory=list)
    agent_outputs: dict = field(default_factory=dict)
    # Phase 6: per-investigation model-call accounting.
    llm_calls: int = 0
    max_llm_calls: int = 8

    def claim_llm_call(self) -> bool:
        """Reserve one model call. Returns False when the cap is exhausted."""
        if self.llm_calls >= self.max_llm_calls:
            return False
        self.llm_calls += 1
        return True
