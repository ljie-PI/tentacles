"""Provider-independent usage shared by operations and report views."""
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0, strict=True)
    output_tokens: int = Field(default=0, ge=0, strict=True)
    total_tokens: int = Field(default=0, ge=0, strict=True)


class UsageRecord(Protocol):
    model: str
    usage: TokenUsage
    cost: float | None


@dataclass
class UsageTotals:
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost: float | None = None
    models: list[str] = field(default_factory=list)

    def record(self, result: UsageRecord) -> None:
        self.merge(UsageTotals(result.usage, result.cost, [result.model]))

    def merge(self, other: "UsageTotals") -> None:
        """Include a completed stage or child run, preserving actual model order."""
        self.usage.input_tokens += other.usage.input_tokens
        self.usage.output_tokens += other.usage.output_tokens
        self.usage.total_tokens += other.usage.total_tokens
        if other.cost is not None:
            self.cost = (self.cost or 0) + other.cost
        for model in other.models:
            if model not in self.models:
                self.models.append(model)
