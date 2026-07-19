import json
import re
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from radar_vagas.canonicalization.normalize import normalize_text


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
    relevance: dict[str, int] = Field(
        default_factory=lambda: {
            "core": 15,
            "adjacent": 7,
            "manual_review": 0,
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
    minimum_interval_between_requests_seconds: float = Field(default=1, ge=0)
    minimum_interval_between_board_requests_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_minimum_interval_alias(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if (
            "minimum_interval_between_requests_seconds" not in data
            and "minimum_interval_between_board_requests_seconds" in data
        ):
            data = {
                **data,
                "minimum_interval_between_requests_seconds": data[
                    "minimum_interval_between_board_requests_seconds"
                ],
            }
        return data


class SearchPlanConfig(BaseModel):
    max_total_requests: int = Field(default=40, ge=1)
    max_total_items: int = Field(default=1_000, ge=1)
    max_duration_seconds: int = Field(default=900, ge=1)


class NetworkConfig(BaseModel):
    http: HttpConfig = Field(default_factory=HttpConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    search_plan: SearchPlanConfig = Field(default_factory=SearchPlanConfig)


class UiConfig(BaseModel):
    timezone: str = "America/Sao_Paulo"
    page_size: int = Field(default=25, ge=5, le=100)
    auto_open_browser: bool = True
    default_job_sort: str = "score"
    default_job_filters: dict[str, str] = Field(default_factory=dict)
    theme_preference: str = "system"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        timezone = value.strip()
        if not timezone:
            raise ValueError("timezone nao pode ficar vazio")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"timezone invalido: {timezone}") from exc
        return timezone

    @field_validator("default_job_sort")
    @classmethod
    def validate_sort(cls, value: str) -> str:
        sort = value.strip().lower()
        if sort not in {"score", "newest", "first-seen"}:
            raise ValueError("default_job_sort deve ser score, newest ou first-seen")
        return sort

    @field_validator("theme_preference")
    @classmethod
    def validate_theme(cls, value: str) -> str:
        theme = value.strip().lower()
        if theme not in {"system", "light", "dark"}:
            raise ValueError("theme_preference deve ser system, light ou dark")
        return theme


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


class SearchQueryConfig(BaseModel):
    key: str
    collector: str
    mode: str
    enabled: bool = True
    priority: int = 100
    tags: list[str] = Field(default_factory=list)
    search_text: str
    filters: dict[str, Any] = Field(default_factory=dict)
    max_pages: int = Field(default=10, ge=1)
    max_items: int = Field(default=200, ge=1)
    hydrate_details: bool = False

    @field_validator("key", "collector", "mode", "search_text")
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
        if collector != "gupy":
            raise ValueError(f"coletor desconhecido para consulta: {value}")
        return collector

    @field_validator("mode")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode != "public_portal":
            raise ValueError(f"modo de consulta desconhecido: {value}")
        return mode

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        tags: list[str] = []
        for tag in value:
            normalized = tag.strip().lower()
            if normalized:
                tags.append(normalized)
        return sorted(set(tags))

    @model_validator(mode="after")
    def validate_filters_and_secrets(self) -> "SearchQueryConfig":
        unsupported = sorted(set(self.filters) - {"country"})
        if unsupported:
            joined = ", ".join(unsupported)
            raise ValueError(f"filtros nao suportados para consulta Gupy: {joined}")
        _raise_if_contains_secret(self.model_dump(mode="json"))
        return self

    @property
    def collection_scope_key(self) -> str:
        return _bounded_slug(f"search-query-{self.key}")

    @property
    def configuration_fingerprint(self) -> str:
        payload = {
            "collector": self.collector,
            "mode": self.mode,
            "search_text": self.search_text,
            "filters": self.filters,
            "max_pages": self.max_pages,
            "max_items": self.max_items,
            "hydrate_details": self.hydrate_details,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()


class SearchQueriesConfig(BaseModel):
    queries: list[SearchQueryConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "SearchQueriesConfig":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for query in self.queries:
            if query.key in seen:
                duplicates.add(query.key)
            seen.add(query.key)
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"keys duplicadas em search_queries: {joined}")
        return self

    def enabled_queries(self) -> list[SearchQueryConfig]:
        return [query for query in self.queries if query.enabled]


class RelevanceWeightsConfig(BaseModel):
    title: int = 5
    department: int = 3
    description: int = 1
    technology: int = 2
    negative: int = 6


class RelevanceThresholdsConfig(BaseModel):
    core: int = 5
    adjacent: int = 4
    manual_review: int = 2
    strong_negative: int = 6


class RelevanceRulesConfig(BaseModel):
    version: str = "2026-07-19.1"
    core_terms: list[str] = Field(
        default_factory=lambda: [
            "analise de dados",
            "dados",
            "data analytics",
            "analytics",
            "business intelligence",
            "bi",
            "power bi",
            "sql",
            "python",
            "engenharia de dados",
            "data engineering",
            "ciencia de dados",
            "data science",
            "inteligencia de negocios",
            "inteligencia de mercado",
            "software",
            "desenvolvedor",
            "desenvolvedora",
            "desenvolvimento",
            "tecnologia",
            "automacao",
            "qa",
            "testes automatizados",
            "produto de tecnologia",
        ]
    )
    strong_adjacent_terms: list[str] = Field(
        default_factory=lambda: [
            "credito",
            "risco",
            "riscos",
            "fraude",
            "pricing",
            "crm",
            "marketing analytics",
            "performance",
            "people analytics",
        ]
    )
    contextual_adjacent_terms: list[str] = Field(
        default_factory=lambda: [
            "operacoes",
            "processos",
            "planejamento",
            "financas",
            "produto",
            "logistica",
            "auditoria",
            "indicadores",
            "relatorios",
            "automacao de processos",
        ]
    )
    supporting_context_terms: list[str] = Field(
        default_factory=lambda: [
            "analise",
            "dados",
            "indicadores",
            "metricas",
            "dashboard",
            "dashboards",
            "power bi",
            "sql",
            "python",
            "automacao",
            "bi",
            "analytics",
            "sistemas",
            "relatorios analiticos",
            "performance quantitativa",
            "segmentacao",
            "campanhas",
        ]
    )
    adjacent_terms: list[str] = Field(default_factory=list)
    technology_terms: list[str] = Field(
        default_factory=lambda: [
            "sql",
            "python",
            "power bi",
            "dashboard",
            "etl",
            "pipeline",
            "api",
            "apis",
            "git",
            "cloud",
        ]
    )
    negative_terms: list[str] = Field(
        default_factory=lambda: [
            "digitacao de dados",
            "cadastro de dados",
            "operador de caixa",
            "auxiliar administrativo",
            "estagio administrativo",
            "recepcao",
            "vendas",
            "contas a pagar",
            "atendimento ao cliente",
            "telemarketing",
            "estoque",
            "logistica operacional",
            "apoio operacional",
            "tarefas administrativas",
            "excel basico",
            "crm operacional",
        ]
    )
    weights: RelevanceWeightsConfig = Field(default_factory=RelevanceWeightsConfig)
    thresholds: RelevanceThresholdsConfig = Field(default_factory=RelevanceThresholdsConfig)
    explanations: dict[str, str] = Field(
        default_factory=lambda: {
            "core": "Sinais principais de dados, tecnologia ou software encontrados.",
            "adjacent": "Sinais adjacentes com uso potencial de analise, indicadores ou processos.",
            "manual_review": "Ha poucos sinais profissionais claros; revisar manualmente.",
            "unrelated": "Nao ha sinais suficientes da area alvo ou ha sinais negativos fortes.",
        }
    )

    @field_validator(
        "core_terms",
        "strong_adjacent_terms",
        "contextual_adjacent_terms",
        "supporting_context_terms",
        "adjacent_terms",
        "technology_terms",
        "negative_terms",
        mode="before",
    )
    @classmethod
    def require_term_list(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("lista de termos esperada")
        return value

    @model_validator(mode="after")
    def merge_legacy_adjacent_terms(self) -> "RelevanceRulesConfig":
        if self.adjacent_terms and not self.strong_adjacent_terms:
            self.strong_adjacent_terms = self.adjacent_terms
        return self


def _raise_if_contains_secret(value: Any, *, path: str = "config") -> None:
    secret_words = {"token", "password", "secret", "cookie", "authorization", "bearer", "login"}
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower()
            if any(secret in normalized_key for secret in secret_words):
                raise ValueError(f"credencial nao permitida em {path}.{key}")
            _raise_if_contains_secret(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _raise_if_contains_secret(nested, path=f"{path}[{index}]")
    elif isinstance(value, str):
        normalized_value = value.lower()
        if any(marker in normalized_value for marker in ("bearer ", "authorization:", "cookie:")):
            raise ValueError(f"credencial nao permitida em {path}")


def _bounded_slug(value: str, *, max_length: int = 120) -> str:
    normalized = normalize_text(value)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "search-query"
    if len(slug) <= max_length:
        return slug
    digest = sha256(slug.encode("utf-8")).hexdigest()[:12]
    return f"{slug[: max_length - 13].rstrip('-')}-{digest}"
