from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from radar_vagas.canonicalization.normalize import normalize_company_name
from radar_vagas.config.schemas import (
    BlockedCompaniesConfig,
    EligibilityRulesConfig,
    ProfileConfig,
    RankingWeightsConfig,
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
