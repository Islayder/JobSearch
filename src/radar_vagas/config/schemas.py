from pydantic import BaseModel, Field


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
