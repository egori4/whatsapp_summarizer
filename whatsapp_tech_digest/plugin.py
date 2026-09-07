from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from .config import DigestConfig

SKIP = "skip"
ALLOW = "allow"


class Spool(Protocol):
    def append_message(self, event: Mapping[str, Any]) -> int: ...


class CollectorPlugin:
    """Healthy-path collector only; non-target events preserve normal dispatch."""

    def __init__(self, config: DigestConfig, spool: Spool, report_failure: Callable[[dict[str, str]], None] | None = None) -> None:
        self.config = config
        self.spool = spool
        self.report_failure = report_failure or (lambda _failure: None)

    def pre_gateway_dispatch(self, event: Mapping[str, Any]) -> str:
        if event.get("chat_jid") != self.config.target_group_jid:
            return ALLOW
        try:
            self.spool.append_message(event)
        except Exception as exc:
            self.report_failure({"kind": "collector_write_failure", "message_id": str(event.get("message_id", "")), "error": type(exc).__name__})
        return SKIP