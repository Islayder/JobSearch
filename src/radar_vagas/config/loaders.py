from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from radar_vagas.canonicalization.normalize import normalize_company_name
from radar_vagas.config.schemas import (
    BlockedCompaniesConfig,
    CompanyBoardsConfig,
    EligibilityRulesConfig,
    NetworkConfig,
    ProfileConfig,
    RankingWeightsConfig,
    RelevanceRulesConfig,
    SearchQueriesConfig,
)


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


@dataclass(frozen=True)
class LoadedProfile:
    profile: ProfileConfig
    path: Path
    used_example: bool


def load_eligibility_rules(config_dir: Path) -> EligibilityRulesConfig:
    path = config_dir / "eligibility_rules.yaml"
    if not path.exists():
        return EligibilityRulesConfig()
    return EligibilityRulesConfig.model_validate(_load_yaml(path))


def load_ranking_weights(config_dir: Path) -> RankingWeightsConfig:
    path = config_dir / "ranking_weights.yaml"
    if not path.exists():
        return RankingWeightsConfig()
    return RankingWeightsConfig.model_validate(_load_yaml(path))


def load_relevance_rules(config_dir: Path) -> RelevanceRulesConfig:
    path = config_dir / "relevance_rules.yaml"
    if not path.exists():
        return RelevanceRulesConfig()
    return RelevanceRulesConfig.model_validate(_load_yaml(path))


def load_network_config(config_dir: Path) -> NetworkConfig:
    preferred = config_dir / "network.yaml"
    fallback = config_dir / "network.example.yaml"
    path = preferred if preferred.exists() else fallback
    if not path.exists():
        return NetworkConfig()
    return NetworkConfig.model_validate(_load_yaml(path))


def load_company_boards(config_dir: Path) -> CompanyBoardsConfig:
    preferred = config_dir / "company_boards.yaml"
    fallback = config_dir / "company_boards.example.yaml"
    local_override = config_dir / "company_boards.local.yaml"
    path = preferred if preferred.exists() else fallback

    raw_boards: list[dict[str, Any]] = []
    if path.exists():
        loaded = _load_yaml(path)
        boards = loaded.get("boards", [])
        if not isinstance(boards, list):
            raise ValueError("company_boards.yaml deve conter uma lista em boards.")
        raw_boards.extend(_ensure_board_dicts(boards, path))
        _raise_duplicate_board_keys(raw_boards, path)

    if local_override.exists():
        loaded_override = _load_yaml(local_override)
        override_boards = loaded_override.get("boards", [])
        if not isinstance(override_boards, list):
            raise ValueError("company_boards.local.yaml deve conter uma lista em boards.")
        override_board_dicts = _ensure_board_dicts(override_boards, local_override)
        _raise_duplicate_board_keys(override_board_dicts, local_override)
        raw_boards = _merge_boards_by_key(raw_boards, override_board_dicts)

    return CompanyBoardsConfig.model_validate({"boards": raw_boards})


def load_search_queries(config_dir: Path) -> SearchQueriesConfig:
    preferred = config_dir / "search_queries.yaml"
    fallback = config_dir / "search_queries.example.yaml"
    local_override = config_dir / "search_queries.local.yaml"
    path = preferred if preferred.exists() else fallback

    raw_queries: list[dict[str, Any]] = []
    if path.exists():
        loaded = _load_yaml(path)
        queries = loaded.get("queries", [])
        if not isinstance(queries, list):
            raise ValueError("search_queries.yaml deve conter uma lista em queries.")
        raw_queries.extend(_ensure_query_dicts(queries, path))
        _raise_duplicate_query_keys(raw_queries, path)

    if local_override.exists():
        loaded_override = _load_yaml(local_override)
        override_queries = loaded_override.get("queries", [])
        if not isinstance(override_queries, list):
            raise ValueError("search_queries.local.yaml deve conter uma lista em queries.")
        override_query_dicts = _ensure_query_dicts(override_queries, local_override)
        _raise_duplicate_query_keys(override_query_dicts, local_override)
        raw_queries = _merge_queries_by_key(raw_queries, override_query_dicts)

    return SearchQueriesConfig.model_validate({"queries": raw_queries})


def load_blocked_companies(config_dir: Path) -> BlockedCompaniesConfig:
    preferred = config_dir / "blocked_companies.yaml"
    fallback = config_dir / "blocked_companies.example.yaml"
    path = preferred if preferred.exists() else fallback
    if not path.exists():
        return BlockedCompaniesConfig()
    return BlockedCompaniesConfig.model_validate(_load_yaml(path))


def load_profile(config_dir: Path, profile_path: Path | None = None) -> LoadedProfile:
    if profile_path is not None:
        return LoadedProfile(
            profile=ProfileConfig.model_validate(_load_yaml(profile_path)),
            path=profile_path,
            used_example=False,
        )

    real_path = config_dir / "profile.yaml"
    if real_path.exists():
        return LoadedProfile(
            profile=ProfileConfig.model_validate(_load_yaml(real_path)),
            path=real_path,
            used_example=False,
        )

    example_path = config_dir / "profile.example.yaml"
    if not example_path.exists():
        raise FileNotFoundError("Nenhum arquivo profile.yaml ou profile.example.yaml encontrado.")

    raw_example = _load_yaml(example_path)
    adapted = {
        "user": {
            "preferred_name": "usuário",
            "city": raw_example.get("location", {}).get("city", "Belo Horizonte"),
            "state": raw_example.get("location", {}).get("state", "MG"),
            "country": raw_example.get("location", {}).get("country", "Brasil"),
        },
        "education": {
            "institution": raw_example.get("institution", "PUC Minas"),
            "course": raw_example.get("course", "Engenharia de Software"),
        },
        "opportunity_priority": [
            value.lower()
            for value in raw_example.get("preferences", {}).get("accepted_employment_types", [])
        ],
        "interest_areas": raw_example.get("interest_areas", []),
    }
    return LoadedProfile(
        profile=ProfileConfig.model_validate(adapted),
        path=example_path,
        used_example=True,
    )


def blocked_company_reasons(config_dir: Path) -> dict[str, str]:
    config = load_blocked_companies(config_dir)
    reasons: dict[str, str] = {}
    for company in config.all_companies:
        names = [company.display_name, *company.aliases]
        for name in names:
            normalized_name = normalize_company_name(name)
            if normalized_name:
                reasons[normalized_name] = company.reason
    return reasons


def _ensure_board_dicts(values: list[Any], path: Path) -> list[dict[str, Any]]:
    boards: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"Board invalido em {path}, item {index}: esperado objeto YAML.")
        boards.append(value)
    return boards


def _merge_boards_by_key(
    base: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for board in [*base, *overrides]:
        key = str(board.get("key", "")).strip()
        if key and key not in order:
            order.append(key)
        merged[key] = {**merged.get(key, {}), **board}
    return [merged[key] for key in order]


def _raise_duplicate_board_keys(values: list[dict[str, Any]], path: Path) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for board in values:
        key = str(board.get("key", "")).strip()
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"Keys duplicadas em {path}: {joined}")


def _ensure_query_dicts(values: list[Any], path: Path) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"Consulta invalida em {path}, item {index}: esperado objeto YAML.")
        queries.append(value)
    return queries


def _merge_queries_by_key(
    base: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for query in [*base, *overrides]:
        key = str(query.get("key", "")).strip()
        if key and key not in order:
            order.append(key)
        merged[key] = {**merged.get(key, {}), **query}
    return [merged[key] for key in order]


def _raise_duplicate_query_keys(values: list[dict[str, Any]], path: Path) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for query in values:
        key = str(query.get("key", "")).strip()
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"Keys duplicadas em {path}: {joined}")
