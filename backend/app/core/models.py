from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False

@dataclass
class AgentResult:
    success: bool
    message: str
    data: Any = None
    tool_calls: list[ToolCall] = field(default_factory=list)

@dataclass
class UltronState:
    mode: str = "IDLE"
    current_task: str | None = None
    last_error: str | None = None
