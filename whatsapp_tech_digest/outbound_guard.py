from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Authorization:
    allowed: bool
    reason: str


class OutboundGuard:
    """Reference model for the independently deployed all-primitive bridge guard.

    In collector-only mode, an enabled ``deny_all_post`` policy denies every POST
    before route or destination dispatch. Read-only routes preserve normal dispatch
    when the guard is initialized with one exact target group JID.
    """

    def __init__(self, *, target_group_jid: str | None, collector_only: bool, deny_all_post: bool, initialized: bool = True) -> None:
        self.target_group_jid = target_group_jid
        self.collector_only = collector_only
        self.deny_all_post = deny_all_post
        self.initialized = initialized

    def authorize(self, method: Any, route: Any, destination_jid: Any) -> Authorization:
        try:
            is_post = method.upper() == "POST"
            target = isinstance(destination_jid, str) and destination_jid == self.target_group_jid
        except Exception:
            return Authorization(False, "guard_evaluation_failure")
        if not self.collector_only:
            return Authorization(True, "normal_mode")
        if not isinstance(self.target_group_jid, str) or not self.target_group_jid.endswith("@g.us") or not self.initialized:
            return Authorization(False, "collector_guard_not_ready")
        if self.deny_all_post and is_post:
            return Authorization(False, "collector_all_post_default_deny")
        if target:
            return Authorization(False, "target_group_outbound_default_deny")
        if is_post:
            return Authorization(False, "collector_policy_load_failure")
        return Authorization(True, "non_target_or_read_only")