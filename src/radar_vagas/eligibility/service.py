from dataclasses import dataclass

from radar_vagas.canonicalization.normalize import (
    is_belo_horizonte,
    normalize_company_name,
    normalize_text,
)
from radar_vagas.config.schemas import EligibilityRulesConfig
from radar_vagas.domain.enums import EligibilityStatus, EmploymentType, JobStatus, WorkModel


@dataclass(frozen=True)
class EligibilityInput:
    company_name: str
    company_aliases: tuple[str, ...]
    company_is_blocked: bool
    job_status: JobStatus
    employment_type: EmploymentType
    work_model: WorkModel
    city: str | None
    state: str | None
    remote_country_scope: str | None
    hours_per_day: float | None
    has_existing_application: bool = False
    has_uninterpreted_course_requirement: bool = False


@dataclass(frozen=True)
class EligibilityResult:
    status: EligibilityStatus
    reason_code: str
    reason_text: str
    rules_version: str


def evaluate_eligibility(
    job: EligibilityInput,
    rules: EligibilityRulesConfig,
    blocked_company_reasons: dict[str, str],
) -> EligibilityResult:
    if _is_company_blocked(job, blocked_company_reasons):
        reason = _blocked_reason(job, blocked_company_reasons)
        return _result(
            EligibilityStatus.INELIGIBLE,
            "COMPANY_BLOCKED",
            reason,
            rules,
        )

    if job.job_status in {JobStatus.DISMISSED, JobStatus.ARCHIVED}:
        return _result(
            EligibilityStatus.TRACK_ONLY,
            "JOB_HISTORY_STATUS",
            "Vaga já descartada ou arquivada; manter apenas acompanhamento histórico.",
            rules,
        )

    if job.has_existing_application:
        return _result(
            EligibilityStatus.TRACK_ONLY,
            "APPLICATION_ALREADY_EXISTS",
            "Já existe candidatura registrada para esta vaga; não recomendar nova candidatura.",
            rules,
        )

    hard_location_result = _hard_location_result(job, rules)
    if hard_location_result is not None:
        return hard_location_result

    result = _employment_result(job, rules)
    if result.status is EligibilityStatus.ELIGIBLE and job.has_uninterpreted_course_requirement:
        return _result(
            EligibilityStatus.MANUAL_REVIEW,
            "COURSE_REQUIREMENT_REVIEW",
            "Há restrição explícita de curso que ainda não é interpretada automaticamente.",
            rules,
        )
    return result


def _is_company_blocked(job: EligibilityInput, blocked_company_reasons: dict[str, str]) -> bool:
    if job.company_is_blocked:
        return True
    normalized_names = _company_normalized_names(job)
    return any(name in blocked_company_reasons for name in normalized_names)


def _blocked_reason(job: EligibilityInput, blocked_company_reasons: dict[str, str]) -> str:
    for normalized_name in _company_normalized_names(job):
        reason = blocked_company_reasons.get(normalized_name)
        if reason is not None:
            return reason
    return "Empresa bloqueada para este perfil."


def _company_normalized_names(job: EligibilityInput) -> tuple[str, ...]:
    names = [job.company_name, *job.company_aliases]
    return tuple(normalize_company_name(name) for name in names if normalize_company_name(name))


def _hard_location_result(
    job: EligibilityInput, rules: EligibilityRulesConfig
) -> EligibilityResult | None:
    if job.work_model is WorkModel.REMOTE:
        remote_scope = _remote_scope_status(job.remote_country_scope, rules)
        if remote_scope == "incompatible":
            return _result(
                EligibilityStatus.INELIGIBLE,
                "REMOTE_SCOPE_INCOMPATIBLE",
                "Vaga remota restrita a país ou localidade incompatível com Brasil.",
                rules,
            )
        return None

    if job.work_model in {WorkModel.HYBRID, WorkModel.ONSITE} and not is_belo_horizonte(
        job.city, job.state
    ):
        return _result(
            EligibilityStatus.INELIGIBLE,
            "LOCATION_NOT_BELO_HORIZONTE",
            "Vaga híbrida ou presencial fora de Belo Horizonte.",
            rules,
        )

    return None


def _employment_result(job: EligibilityInput, rules: EligibilityRulesConfig) -> EligibilityResult:
    if job.employment_type is EmploymentType.INTERNSHIP:
        return _internship_result(job, rules)
    if job.employment_type is EmploymentType.TRAINEE:
        return _trainee_result(job, rules)
    if job.employment_type is EmploymentType.JUNIOR:
        return _junior_result(job, rules)
    if job.employment_type is EmploymentType.SCHOLARSHIP:
        return _result(
            EligibilityStatus.MANUAL_REVIEW,
            "SCHOLARSHIP_REQUIRES_REVIEW",
            "Bolsa de inovação deve ser revisada manualmente nesta etapa.",
            rules,
        )
    return _result(
        EligibilityStatus.MANUAL_REVIEW,
        "EMPLOYMENT_TYPE_REQUIRES_REVIEW",
        "Tipo de vínculo não interpretado automaticamente nesta etapa.",
        rules,
    )


def _internship_result(job: EligibilityInput, rules: EligibilityRulesConfig) -> EligibilityResult:
    if job.work_model is WorkModel.REMOTE:
        return _remote_result_or_review(job, rules, "INTERNSHIP_REMOTE_BRAZIL")
    if job.work_model in {WorkModel.HYBRID, WorkModel.ONSITE}:
        return _result(
            EligibilityStatus.ELIGIBLE,
            "INTERNSHIP_BELO_HORIZONTE",
            "Estágio em Belo Horizonte compatível com as regras.",
            rules,
        )
    return _manual_unknown_work_model(rules)


def _trainee_result(job: EligibilityInput, rules: EligibilityRulesConfig) -> EligibilityResult:
    if job.work_model is WorkModel.REMOTE:
        return _remote_result_or_review(job, rules, "TRAINEE_REMOTE_BRAZIL")
    if job.work_model is WorkModel.HYBRID:
        return _result(
            EligibilityStatus.ELIGIBLE,
            "TRAINEE_HYBRID_BELO_HORIZONTE",
            "Trainee híbrido em Belo Horizonte compatível com as regras.",
            rules,
        )
    if job.work_model is WorkModel.ONSITE:
        if job.hours_per_day is None:
            return _result(
                EligibilityStatus.MANUAL_REVIEW,
                "TRAINEE_ONSITE_HOURS_UNKNOWN",
                "Trainee presencial em Belo Horizonte sem jornada diária informada.",
                rules,
            )
        if job.hours_per_day > rules.trainee_max_onsite_hours_per_day:
            return _result(
                EligibilityStatus.INELIGIBLE,
                "TRAINEE_ONSITE_HOURS_TOO_HIGH",
                "Trainee presencial em Belo Horizonte com mais de 6 horas diárias.",
                rules,
            )
        return _result(
            EligibilityStatus.ELIGIBLE,
            "TRAINEE_ONSITE_BELO_HORIZONTE_UP_TO_6H",
            "Trainee presencial em Belo Horizonte com jornada de até 6 horas diárias.",
            rules,
        )
    return _manual_unknown_work_model(rules)


def _junior_result(job: EligibilityInput, rules: EligibilityRulesConfig) -> EligibilityResult:
    if job.work_model is WorkModel.REMOTE:
        return _remote_result_or_review(job, rules, "JUNIOR_REMOTE_BRAZIL")
    if job.work_model is WorkModel.HYBRID:
        return _result(
            EligibilityStatus.ELIGIBLE,
            "JUNIOR_HYBRID_BELO_HORIZONTE",
            "Vaga júnior híbrida em Belo Horizonte compatível com as regras.",
            rules,
        )
    if job.work_model is WorkModel.ONSITE:
        return _result(
            EligibilityStatus.INELIGIBLE,
            "JUNIOR_ONSITE_NOT_ALLOWED",
            "Vaga júnior presencial não é aceita, inclusive em Belo Horizonte.",
            rules,
        )
    return _manual_unknown_work_model(rules)


def _remote_result_or_review(
    job: EligibilityInput, rules: EligibilityRulesConfig, reason_code: str
) -> EligibilityResult:
    remote_scope = _remote_scope_status(job.remote_country_scope, rules)
    if remote_scope == "brazil":
        return _result(
            EligibilityStatus.ELIGIBLE,
            reason_code,
            "Vaga remota explicitamente disponível para residentes no Brasil.",
            rules,
        )
    return _result(
        EligibilityStatus.MANUAL_REVIEW,
        "REMOTE_SCOPE_UNKNOWN",
        "Vaga remota sem indicação clara de aceite para residentes no Brasil.",
        rules,
    )


def _remote_scope_status(scope: str | None, rules: EligibilityRulesConfig) -> str:
    normalized_scope = normalize_text(scope)
    if not normalized_scope:
        return "unknown"
    accepted = {normalize_text(value) for value in rules.accepted_remote_country_scopes}
    if normalized_scope in accepted:
        return "brazil"
    return "incompatible"


def _manual_unknown_work_model(rules: EligibilityRulesConfig) -> EligibilityResult:
    return _result(
        EligibilityStatus.MANUAL_REVIEW,
        "WORK_MODEL_UNKNOWN",
        "Modalidade não interpretada automaticamente nesta etapa.",
        rules,
    )


def _result(
    status: EligibilityStatus,
    reason_code: str,
    reason_text: str,
    rules: EligibilityRulesConfig,
) -> EligibilityResult:
    return EligibilityResult(
        status=status,
        reason_code=reason_code,
        reason_text=reason_text,
        rules_version=rules.rules_version,
    )
