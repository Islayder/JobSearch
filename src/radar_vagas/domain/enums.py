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


class ApplicationStage(ReadableEnum):
    APPLIED = "APPLIED"
    AWAITING_UPDATE = "AWAITING_UPDATE"
    ASSESSMENT_RECEIVED = "ASSESSMENT_RECEIVED"
    ASSESSMENT_COMPLETED = "ASSESSMENT_COMPLETED"
    CASE_RECEIVED = "CASE_RECEIVED"
    CASE_SUBMITTED = "CASE_SUBMITTED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    OFFER_RECEIVED = "OFFER_RECEIVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ReviewState(ReadableEnum):
    UNREVIEWED = "UNREVIEWED"
    SEEN = "SEEN"
    SHORTLISTED = "SHORTLISTED"
    DISMISSED = "DISMISSED"
    APPLIED = "APPLIED"


class ReviewEventType(ReadableEnum):
    SEEN = "SEEN"
    SHORTLISTED = "SHORTLISTED"
    DISMISSED = "DISMISSED"
    RESTORED = "RESTORED"
    APPLIED = "APPLIED"


class ApplicationEventType(ReadableEnum):
    SUBMITTED = "SUBMITTED"
    CONFIRMATION_RECEIVED = "CONFIRMATION_RECEIVED"
    ASSESSMENT_INVITED = "ASSESSMENT_INVITED"
    ASSESSMENT_COMPLETED = "ASSESSMENT_COMPLETED"
    INTERVIEW_INVITED = "INTERVIEW_INVITED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    CASE_RECEIVED = "CASE_RECEIVED"
    CASE_SUBMITTED = "CASE_SUBMITTED"
    PROCESS_UPDATE = "PROCESS_UPDATE"
    REJECTED = "REJECTED"
    OFFER_RECEIVED = "OFFER_RECEIVED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"


class ApplicationMatchKind(ReadableEnum):
    EXACT = "EXACT"
    PROBABLE = "PROBABLE"
    UNMATCHED = "UNMATCHED"
    CONFLICT = "CONFLICT"


class ApplicationMatchStatus(ReadableEnum):
    LINKED = "LINKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IGNORED = "IGNORED"


class RequirementKind(ReadableEnum):
    MANDATORY = "MANDATORY"
    DESIRABLE = "DESIRABLE"
    UNKNOWN = "UNKNOWN"


class RequirementMatchStatus(ReadableEnum):
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    NOT_PROVEN = "NOT_PROVEN"
    NOT_MATCHED = "NOT_MATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class ProfileEvidenceType(ReadableEnum):
    SKILL = "SKILL"
    EXPERIENCE = "EXPERIENCE"
    PROJECT = "PROJECT"
    EDUCATION = "EDUCATION"
    LANGUAGE = "LANGUAGE"
    RESUME = "RESUME"


class ExtractedBlockType(ReadableEnum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST_ITEM = "LIST_ITEM"
    TABLE_CELL = "TABLE_CELL"
    PAGE_BREAK = "PAGE_BREAK"


class ResumeImportStatus(ReadableEnum):
    EXTRACTING = "EXTRACTING"
    REVIEWING = "REVIEWING"
    CONFIRMED = "CONFIRMED"
    DISCARDED = "DISCARDED"
    FAILED = "FAILED"


class ResumeImportCandidateType(ReadableEnum):
    HEADLINE = "HEADLINE"
    SUMMARY = "SUMMARY"
    SKILL = "SKILL"
    EXPERIENCE = "EXPERIENCE"
    PROJECT = "PROJECT"
    EDUCATION = "EDUCATION"
    LANGUAGE = "LANGUAGE"
    AMBIGUOUS = "AMBIGUOUS"


class ResumeImportDecision(ReadableEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    REMOVED = "REMOVED"


class ResumeImportConfidenceLabel(ReadableEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ApplicationGuardDecision(ReadableEnum):
    ALLOW_PREPARATION = "ALLOW_PREPARATION"
    TRACK_ONLY = "TRACK_ONLY"
    BLOCK_ALREADY_APPLIED = "BLOCK_ALREADY_APPLIED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    BLOCK_DISMISSED = "BLOCK_DISMISSED"
    BLOCK_CLOSED = "BLOCK_CLOSED"


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


class CareerEventType(ReadableEnum):
    APPLICATION_DEADLINE = "APPLICATION_DEADLINE"
    ASSESSMENT = "ASSESSMENT"
    ASSESSMENT_DEADLINE = "ASSESSMENT_DEADLINE"
    CASE_DEADLINE = "CASE_DEADLINE"
    INTERVIEW = "INTERVIEW"
    GROUP_DYNAMICS = "GROUP_DYNAMICS"
    DOCUMENT_DEADLINE = "DOCUMENT_DEADLINE"
    OFFER_RESPONSE_DEADLINE = "OFFER_RESPONSE_DEADLINE"
    FOLLOW_UP = "FOLLOW_UP"
    CUSTOM = "CUSTOM"


class CareerEventSource(ReadableEnum):
    MANUAL = "MANUAL"
    JOB_DESCRIPTION = "JOB_DESCRIPTION"
    EMAIL = "EMAIL"
    ESTIMATED = "ESTIMATED"


class CareerEventConfirmationStatus(ReadableEnum):
    SUGGESTED = "SUGGESTED"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
