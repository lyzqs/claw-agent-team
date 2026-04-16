from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal['ceo', 'pm', 'dev', 'qa', 'ops']
VALID_ROUTE_TARGETS = {'ceo', 'pm', 'dev', 'qa', 'ops', 'close', 'human_queue'}


@dataclass(frozen=True)
class RoutingDecision:
    allowed: bool
    reason: str
    requires_human_queue: bool = False
    requires_ceo: bool = False


def route_issue(*, from_role: Role, to_role: str, issue_type: str = 'normal', risk_level: str = 'normal') -> RoutingDecision:
    if to_role not in VALID_ROUTE_TARGETS:
        return RoutingDecision(
            allowed=False,
            reason=f'unknown route target: {to_role}',
        )

    return RoutingDecision(
        allowed=True,
        reason=f'route allowed: {from_role} -> {to_role}',
        requires_human_queue=(to_role == 'human_queue'),
        requires_ceo=(to_role == 'ceo'),
    )
