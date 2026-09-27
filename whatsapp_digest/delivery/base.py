"""Delivery interfaces for WhatsApp Technical Digest v2."""
from __future__ import annotations

from typing import Protocol

from ..config import WorkflowConfig


class DeliveryError(RuntimeError):
    pass


class DeliveryChannel(Protocol):
    def send_digest(
        self,
        workflow: WorkflowConfig,
        *,
        subject: str,
        text: str,
        html: str,
        raw_text: str | None,
        run_id: str,
    ) -> None:
        ...

    def send_failure(
        self,
        workflow: WorkflowConfig,
        *,
        run_id: str,
        stage: str,
        reason: str,
        date: str,
    ) -> None:
        ...
