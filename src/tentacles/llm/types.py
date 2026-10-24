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
        self.usage.input_tokens += result.usage.input_tokens
        self.usage.output_tokens += result.usage.output_tokens
        self.usage.total_tokens += result.usage.total_tokens
        if result.cost is not None:
            self.cost = (self.cost or 0) + result.cost
        if result.model not in self.models:
            self.models.append(result.model)
