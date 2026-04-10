from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal['ceo', 'pm', 'dev', 'qa', 'ops']


@dataclass(frozen=True)
class RoutingDecision:
    allowed: bool
    reason: str
    requires_human_queue: bool = False
    requires_ceo: bool = False


_ALLOWED_NEXT: dict[Role, set[str]] = {
    'ceo': {'pm', 'close', 'human_queue'},
    'pm': {'dev', 'qa', 'ceo', 'close'},
    'dev': {'qa', 'pm', 'human_queue', 'ceo'},
    'qa': {'dev', 'ops', 'ceo', 'close'},
    'ops': {'pm', 'ceo', 'human_queue', 'close'},
}


def route_issue(*, from_role: Role, to_role: str, issue_type: str = 'normal', risk_level: str = 'normal') -> RoutingDecision:
    if to_role not in _ALLOWED_NEXT[from_role]:
        return RoutingDecision(
            allowed=False,
            reason=f'route not allowed: {from_role} -> {to_role}',
        )

    # hard constraints
    if risk_level == 'high' and to_role not in {'ceo', 'human_queue'}:
        return RoutingDecision(
            allowed=False,
            reason='high risk routes must escalate to CEO or Human Queue',
            requires_ceo=True,
        )

    if issue_type == 'production_change' and from_role in {'dev', 'ops'} and to_role not in {'ceo', 'human_queue'}:
        return RoutingDecision(
            allowed=False,
            reason='production changes require CEO or Human Queue approval',
            requires_human_queue=True,
            requires_ceo=True,
        )

    return RoutingDecision(
        allowed=True,
        reason=f'route allowed: {from_role} -> {to_role}',
        requires_human_queue=(to_role == 'human_queue'),
        requires_ceo=(to_role == 'ceo'),
    )
