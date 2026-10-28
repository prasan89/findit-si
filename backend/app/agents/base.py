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


@dataclass
class AgentResult(Generic[T]):
    agent_name: str
    status: AgentStatus
    output: Optional[T]
    duration_ms: float
    error: Optional[str] = None
    tokens_used: int = 0
    cost_usd: float = 0.0

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
