from enum import StrEnum


class ReadableEnum(StrEnum):
    """String enum stored with readable values."""

    @classmethod
    def choices(cls) -> list[str]:
        return [member.value.lower() for member in cls]


def parse_enum_value[EnumType: ReadableEnum](enum_type: type[EnumType], value: str) -> EnumType:
    normalized = value.strip().replace("-", "_").upper()
    for member in enum_type:
        if member.name == normalized or member.value == normalized:
            return member
    allowed = ", ".join(enum_type.choices())
    raise ValueError(f"valor inválido '{value}'. Valores aceitos: {allowed}.")


class EmploymentType(ReadableEnum):
    INTERNSHIP = "INTERNSHIP"
    TRAINEE = "TRAINEE"
    JUNIOR = "JUNIOR"
    SCHOLARSHIP = "SCHOLARSHIP"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class WorkModel(ReadableEnum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ONSITE = "ONSITE"
    UNKNOWN = "UNKNOWN"


class JobStatus(ReadableEnum):
    NEW = "NEW"
    PENDING_REVIEW = "PENDING_REVIEW"
    ELIGIBLE = "ELIGIBLE"
    RECOMMENDED = "RECOMMENDED"
    SEEN = "SEEN"
    DISMISSED = "DISMISSED"
    ARCHIVED = "ARCHIVED"
    APPLIED = "APPLIED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class EligibilityStatus(ReadableEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    TRACK_ONLY = "TRACK_ONLY"


class ApplicationStatus(ReadableEnum):
    PREPARING = "PREPARING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    READY = "READY"
    SUBMITTED = "SUBMITTED"
    TEST = "TEST"
    INTERVIEW = "INTERVIEW"
    FINAL_STAGE = "FINAL_STAGE"
    REJECTED = "REJECTED"
    OFFER = "OFFER"
    WITHDRAWN = "WITHDRAWN"
    CLOSED = "CLOSED"


class SourceRunStatus(ReadableEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PostingStatus(ReadableEnum):
    NEW = "NEW"
    LINKED = "LINKED"
    PROBABLE_DUPLICATE = "PROBABLE_DUPLICATE"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    CLOSED = "CLOSED"


class DuplicateKind(ReadableEnum):
    EXACT = "EXACT"
    PROBABLE = "PROBABLE"
    DISTINCT = "DISTINCT"


class CollectionAuthority(ReadableEnum):
    AUTHORITATIVE_BOARD = "AUTHORITATIVE_BOARD"
    DISCOVERY_QUERY = "DISCOVERY_QUERY"
    SINGLE_PAGE = "SINGLE_PAGE"


class RelevanceStatus(ReadableEnum):
    CORE = "CORE"
    ADJACENT = "ADJACENT"
    UNRELATED = "UNRELATED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
