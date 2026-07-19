from pathlib import Path

import pytest
import yaml

from radar_vagas.config.loaders import (
    blocked_company_reasons,
    load_blocked_companies,
    load_profile,
)


def test_load_profile_yaml(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """
user:
  preferred_name: Islayder
  city: Belo Horizonte
  state: MG
  country: Brasil
education:
  institution: PUC Minas
  course: Engenharia de Software
opportunity_priority: [internship]
""",
        encoding="utf-8",
    )

    loaded = load_profile(tmp_path)

    assert loaded.used_example is False
    assert loaded.profile.user.preferred_name == "Islayder"
    assert loaded.profile.education.course == "Engenharia de Software"


def test_profile_fallback_to_example_is_explicit(tmp_path: Path) -> None:
    (tmp_path / "profile.example.yaml").write_text(
        """
course: Engenharia de Software
institution: PUC Minas
location:
  city: Belo Horizonte
  state: MG
  country: Brasil
preferences:
  accepted_employment_types: [INTERNSHIP]
interest_areas: [dados]
""",
        encoding="utf-8",
    )

    loaded = load_profile(tmp_path)

    assert loaded.used_example is True
    assert loaded.path.name == "profile.example.yaml"


def test_empty_blocked_companies_file_is_valid(tmp_path: Path) -> None:
    (tmp_path / "blocked_companies.yaml").write_text("companies: []\n", encoding="utf-8")

    loaded = load_blocked_companies(tmp_path)

    assert loaded.all_companies == []
    assert blocked_company_reasons(tmp_path) == {}


def test_blocked_company_aliases_are_normalized(tmp_path: Path) -> None:
    (tmp_path / "blocked_companies.yaml").write_text(
        """
companies:
  - canonical_name: Empresa Exemplo
    reason: former_employer
    aliases:
      - Empresa Exemplo LTDA
""",
        encoding="utf-8",
    )

    reasons = blocked_company_reasons(tmp_path)

    assert reasons["empresa exemplo"] == "former_employer"


def test_invalid_yaml_raises_clear_parser_error(tmp_path: Path) -> None:
    (tmp_path / "blocked_companies.yaml").write_text("companies: [\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_blocked_companies(tmp_path)


def test_profile_missing_required_fields_fails(tmp_path: Path) -> None:
    (tmp_path / "profile.yaml").write_text("user: {}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_profile(tmp_path)
