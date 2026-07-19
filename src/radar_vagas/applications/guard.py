from __future__ import annotations

from dataclasses import dataclass

from radar_vagas.domain.enums import (
    ApplicationGuardDecision,
    ApplicationStatus,
    JobStatus,
    ReviewState,
)
from radar_vagas.persistence.models import Job


@dataclass(frozen=True)
class ApplicationGuardResult:
    decision: ApplicationGuardDecision
    reason_code: str
    reason_text: str

    @property
    def allows_preparation(self) -> bool:
        return self.decision is ApplicationGuardDecision.ALLOW_PREPARATION


class ApplicationGuard:
    def evaluate(self, job: Job) -> ApplicationGuardResult:
        review_state = job.review_state.state if job.review_state is not None else None
        application_statuses = {application.status for application in job.applications}

        if job.status is JobStatus.CLOSED:
            return _result(
                ApplicationGuardDecision.BLOCK_CLOSED,
                "JOB_CLOSED",
                "Vaga encerrada; nao preparar nova candidatura.",
            )
        if job.status is JobStatus.DISMISSED or review_state is ReviewState.DISMISSED:
            return _result(
                ApplicationGuardDecision.BLOCK_DISMISSED,
                "JOB_DISMISSED",
                "Vaga descartada pelo usuario.",
            )
        if job.status is JobStatus.APPLIED or application_statuses:
            if application_statuses == {ApplicationStatus.WITHDRAWN}:
                return _result(
                    ApplicationGuardDecision.TRACK_ONLY,
                    "APPLICATION_WITHDRAWN",
                    "Candidatura retirada; manter acompanhamento historico.",
                )
            return _result(
                ApplicationGuardDecision.BLOCK_ALREADY_APPLIED,
                "APPLICATION_ALREADY_EXISTS",
                "Ja existe candidatura registrada para esta vaga.",
            )
        if job.status is JobStatus.ARCHIVED:
            return _result(
                ApplicationGuardDecision.TRACK_ONLY,
                "JOB_ARCHIVED",
                "Vaga arquivada pelas regras atuais.",
            )
        if job.status is JobStatus.PENDING_REVIEW:
            return _result(
                ApplicationGuardDecision.MANUAL_REVIEW,
                "JOB_PENDING_REVIEW",
                "Vaga exige revisao manual antes de candidatura.",
            )
        return _result(
            ApplicationGuardDecision.ALLOW_PREPARATION,
            "READY_FOR_REVIEW",
            "Vaga pode entrar na fila de revisao humana.",
        )


def _result(
    decision: ApplicationGuardDecision,
    reason_code: str,
    reason_text: str,
) -> ApplicationGuardResult:
    return ApplicationGuardResult(
        decision=decision,
        reason_code=reason_code,
        reason_text=reason_text,
    )
