from pydantic import BaseModel, Field, field_validator, model_validator


class EligibilityRulesConfig(BaseModel):
    rules_version: str = "2026-07-18"
    target_city: str = "Belo Horizonte"
    target_state: str = "MG"
    target_country: str = "Brasil"
    accepted_remote_country_scopes: list[str] = Field(
        default_factory=lambda: ["Brasil", "Brazil", "BR"]
    )
    manual_review_when_remote_scope_unknown: bool = True
    trainee_max_onsite_hours_per_day: int = 6
    junior_allows_onsite: bool = False
    metropolitan_cities_not_bh: list[str] = Field(
        default_factory=lambda: [
            "Contagem",
            "Betim",
            "Nova Lima",
            "Ribeirão das Neves",
            "Sabará",
        ]
    )


class RankingAdditionalWeights(BaseModel):
    salary_disclosed: int = 5
    benefit_keyword_max: int = 5
    benefit_keyword_points: int = 1
    freshness_days: int = 7
    freshness: int = 5
    hours_disclosed: int = 2


class RankingWeightsConfig(BaseModel):
    recommended_min_score: int = 70
    employment_type: dict[str, int] = Field(
        default_factory=lambda: {
            "INTERNSHIP": 40,
            "TRAINEE": 25,
            "JUNIOR": 15,
            "SCHOLARSHIP": 10,
            "OTHER": 0,
            "UNKNOWN": 0,
        }
    )
    work_model: dict[str, int] = Field(
        default_factory=lambda: {
            "remote_brazil": 30,
            "hybrid_belo_horizonte": 20,
            "onsite_belo_horizonte": 10,
        }
    )
    additional: RankingAdditionalWeights = Field(default_factory=RankingAdditionalWeights)
    benefit_keywords: list[str] = Field(
        default_factory=lambda: [
            "benefício",
            "benefícios",
            "vale",
            "alimentação",
            "refeição",
            "transporte",
            "saúde",
            "odontológico",
            "seguro",
            "gympass",
            "auxílio",
        ]
    )


class BlockedCompanyConfig(BaseModel):
    name: str | None = None
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    reason: str = "Empresa bloqueada."

    @property
    def display_name(self) -> str:
        return self.canonical_name or self.name or ""


class BlockedCompaniesConfig(BaseModel):
    blocked_companies: list[BlockedCompanyConfig] = Field(default_factory=list)
    companies: list[BlockedCompanyConfig] = Field(default_factory=list)

    @property
    def all_companies(self) -> list[BlockedCompanyConfig]:
        return [*self.companies, *self.blocked_companies]


class UserProfile(BaseModel):
    preferred_name: str
    city: str
    state: str
    country: str


class EducationProfile(BaseModel):
    institution: str
    course: str


class LocationPreferences(BaseModel):
    priority: list[str] = Field(default_factory=list)
    accepted_onsite_cities: list[str] = Field(default_factory=list)
    accepted_hybrid_cities: list[str] = Field(default_factory=list)
    treat_metro_area_as_belo_horizonte: bool = False


class CompensationPreferences(BaseModel):
    minimum_eliminatory_value: float | None = None
    disclosed_compensation_affects_ranking: bool = True
    missing_compensation_is_eliminatory: bool = False


class GrowthPreferences(BaseModel):
    accept_adjacent_areas: bool = True
    growth_paths: list[str] = Field(default_factory=list)


class ProfileConfig(BaseModel):
    user: UserProfile
    education: EducationProfile
    opportunity_priority: list[str] = Field(default_factory=list)
    location_preferences: LocationPreferences = Field(default_factory=LocationPreferences)
    rules_by_type: dict[str, object] = Field(default_factory=dict)
    interest_areas: list[str] = Field(default_factory=list)
    compensation: CompensationPreferences = Field(default_factory=CompensationPreferences)
    growth: GrowthPreferences = Field(default_factory=GrowthPreferences)


class HttpConfig(BaseModel):
    user_agent: str = "RadarVagas/0.3 - personal job search tool"
    connect_timeout_seconds: float = Field(default=10, gt=0)
    read_timeout_seconds: float = Field(default=30, gt=0)
    write_timeout_seconds: float = Field(default=10, gt=0)
    pool_timeout_seconds: float = Field(default=10, gt=0)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_response_bytes: int = Field(default=5_242_880, ge=1024)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])

    @field_validator("allowed_ports")
    @classmethod
    def validate_allowed_ports(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("allowed_ports nao pode ficar vazio")
        invalid = [port for port in value if port < 1 or port > 65535]
        if invalid:
            raise ValueError(f"portas invalidas: {invalid}")
        return sorted(set(value))


class CollectionConfig(BaseModel):
    default_max_items: int = Field(default=500, ge=1, le=10_000)
    max_parallel_requests: int = Field(default=3, ge=1, le=10)
    close_after_missing_successful_runs: int = Field(default=2, ge=1, le=10)
    minimum_interval_between_board_requests_seconds: float = Field(default=1, ge=0)


class NetworkConfig(BaseModel):
    http: HttpConfig = Field(default_factory=HttpConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)


class BoardConfig(BaseModel):
    key: str
    company_name: str
    collector: str
    board_token: str | None = None
    url: str | None = None
    enabled: bool = True
    priority: int = 100
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("key", "company_name", "collector")
    @classmethod
    def require_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("campo obrigatorio vazio")
        return stripped

    @field_validator("collector")
    @classmethod
    def normalize_collector(cls, value: str) -> str:
        collector = value.strip().lower()
        allowed = {"jobposting", "greenhouse", "lever"}
        if collector not in allowed:
            raise ValueError(f"coletor desconhecido: {value}")
        return collector

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        tags = []
        for tag in value:
            normalized = tag.strip().lower()
            if normalized:
                tags.append(normalized)
        return sorted(set(tags))

    @model_validator(mode="after")
    def validate_collector_fields(self) -> "BoardConfig":
        if self.collector in {"greenhouse", "lever"} and not self.board_token:
            raise ValueError(f"board_token e obrigatorio para {self.collector}")
        if self.collector == "jobposting" and not self.url:
            raise ValueError("url e obrigatoria para jobposting")
        return self


class CompanyBoardsConfig(BaseModel):
    boards: list[BoardConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "CompanyBoardsConfig":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for board in self.boards:
            if board.key in seen:
                duplicates.add(board.key)
            seen.add(board.key)
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"keys duplicadas em company_boards: {joined}")
        return self

    def enabled_boards(self) -> list[BoardConfig]:
        return [board for board in self.boards if board.enabled]
