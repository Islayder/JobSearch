from datetime import UTC, datetime, timedelta

from radar_vagas.deduplication.service import DuplicateCandidate, classify_canonical_duplicate
from radar_vagas.domain.enums import DuplicateKind, WorkModel


def candidate(
    *,
    company: str = "empresa teste",
    title: str = "estagio em dados",
    city: str | None = "Belo Horizonte",
    work_model: WorkModel = WorkModel.HYBRID,
    published_at: datetime | None = datetime(2026, 7, 18, tzinfo=UTC),
    application_url: str | None = "https://jobs.example.test/1",
    content_hash: str | None = None,
) -> DuplicateCandidate:
    return DuplicateCandidate(
        normalized_company=company,
        normalized_title=title,
        city=city,
        work_model=work_model,
        published_at=published_at,
        application_url=application_url,
        content_hash=content_hash,
    )


def test_exact_duplicate_by_url() -> None:
    first = candidate(application_url="https://jobs.example.test/1?utm_source=x")
    second = candidate(application_url="https://jobs.example.test/1")
    assert classify_canonical_duplicate(first, second) is DuplicateKind.EXACT


def test_exact_duplicate_by_hash() -> None:
    first = candidate(application_url="https://jobs.example.test/1", content_hash="abc")
    second = candidate(application_url="https://jobs.example.test/2", content_hash="abc")
    assert classify_canonical_duplicate(first, second) is DuplicateKind.EXACT


def test_probable_duplicate_between_sources_is_not_exact() -> None:
    first = candidate(application_url="https://source-a.example.test/1")
    second = candidate(application_url="https://source-b.example.test/2")
    assert classify_canonical_duplicate(first, second) is DuplicateKind.PROBABLE


def test_distinct_when_dates_are_far_or_identity_differs() -> None:
    first = candidate()
    far = candidate(
        published_at=datetime(2026, 7, 18, tzinfo=UTC) + timedelta(days=30),
        application_url="https://jobs.example.test/2",
    )
    other_company = candidate(company="outra empresa")
    assert classify_canonical_duplicate(first, far) is DuplicateKind.DISTINCT
    assert classify_canonical_duplicate(first, other_company) is DuplicateKind.DISTINCT
