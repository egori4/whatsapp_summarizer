"""SMTP email delivery for WhatsApp Technical Digest v2."""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import logging
import os
import smtplib
from typing import Any, Callable

from .base import DeliveryError
from ..config import AppConfig, WorkflowConfig

logger = logging.getLogger(__name__)


class EmailDelivery:
    def __init__(
        self,
        config: AppConfig,
        *,
        smtp_factory: Callable[..., Any] = smtplib.SMTP,
    ):
        self.config = config
        self._smtp_factory = smtp_factory

    def _recipients(self, workflow: WorkflowConfig) -> list[str]:
        return list(workflow.email["to"]) + list(workflow.email["cc"])

    def _connect_and_send(self, message: EmailMessage, recipients: list[str]) -> None:
        cfg = self.config.email
        password_env = cfg["password_env"]
        password = os.environ.get(password_env)
        if cfg.get("username") and password is None:
            raise DeliveryError(f"SMTP password environment variable is not set: {password_env}")

        run_id = message.get("X-WhatsApp-Digest-Run-ID", "unknown")
        logger.info(
            "smtp connect run_id=%s host=%s port=%s starttls=%s username_set=%s sender=%s recipients=%s",
            run_id,
            cfg["host"],
            cfg["port"],
            cfg["starttls"],
            bool(cfg.get("username")),
            cfg["sender"],
            ",".join(recipients),
        )
        try:
            with self._smtp_factory(
                cfg["host"],
                cfg["port"],
                timeout=cfg["timeout_seconds"],
            ) as smtp:
                smtp.ehlo()
                if cfg["starttls"]:
                    smtp.starttls()
                    smtp.ehlo()
                if cfg.get("username"):
                    smtp.login(cfg["username"], password or "")
                refused = smtp.send_message(message, to_addrs=recipients)
                if refused:
                    refused_list = ",".join(sorted(str(address) for address in refused))
                    logger.error(
                        "smtp refused recipients run_id=%s recipients=%s",
                        run_id,
                        refused_list,
                    )
                    raise DeliveryError(f"SMTP refused recipients: {refused_list}")
        except DeliveryError:
            raise
        except Exception as exc:
            logger.exception(
                "smtp transaction failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
            raise DeliveryError(f"SMTP delivery failed: {type(exc).__name__}: {exc}") from exc

        logger.info(
            "smtp accepted run_id=%s recipient_count=%d",
            run_id,
            len(recipients),
        )

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
        recipients = self._recipients(workflow)
        message = EmailMessage()
        message["From"] = self.config.email["sender"]
        message["To"] = ", ".join(workflow.email["to"])
        if workflow.email["cc"]:
            message["Cc"] = ", ".join(workflow.email["cc"])
        message["Subject"] = subject
        message["X-WhatsApp-Digest-Run-ID"] = run_id
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        if workflow.email["attach_raw_messages"] and raw_text is not None:
            message.add_attachment(
                raw_text,
                subtype="plain",
                filename=f"{workflow.id}-{run_id}-raw.txt",
            )
        self._connect_and_send(message, recipients)

    def send_failure(
        self,
        workflow: WorkflowConfig,
        *,
        run_id: str,
        stage: str,
        reason: str,
        date: str,
    ) -> None:
        recipients = self._recipients(workflow)
        message = EmailMessage()
        message["From"] = self.config.email["sender"]
        message["To"] = ", ".join(workflow.email["to"])
        if workflow.email["cc"]:
            message["Cc"] = ", ".join(workflow.email["cc"])
        message["Subject"] = f"[Digest FAILED] {workflow.name} — {date}"
        message["X-WhatsApp-Digest-Run-ID"] = run_id
        message.set_content(
            "\n".join(
                [
                    f"Workflow: {workflow.name}",
                    f"Run ID: {run_id}",
                    f"Failure stage: {stage}",
                    f"Reason: {reason}",
                    "",
                    "No raw WhatsApp source content is included in this failure notification.",
                ]
            )
            + "\n"
        )
        self._connect_and_send(message, recipients)

    def send_test(self, workflow: WorkflowConfig) -> str:
        run_id = "email-test-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        recipients = self._recipients(workflow)
        message = EmailMessage()
        message["From"] = self.config.email["sender"]
        message["To"] = ", ".join(workflow.email["to"])
        if workflow.email["cc"]:
            message["Cc"] = ", ".join(workflow.email["cc"])
        message["Subject"] = f"[Digest SMTP Test] {workflow.name}"
        message["X-WhatsApp-Digest-Run-ID"] = run_id
        message.set_content(
            "\n".join(
                [
                    "WhatsApp Technical Digest SMTP test.",
                    "",
                    f"Workflow: {workflow.name}",
                    f"Run ID: {run_id}",
                    "This message contains no WhatsApp source content and does not change checkpoints.",
                ]
            )
            + "\n"
        )
        self._connect_and_send(message, recipients)
        return run_id
