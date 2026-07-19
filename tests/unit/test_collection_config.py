from __future__ import annotations

from pathlib import Path

import pytest

from radar_vagas.config.loaders import load_company_boards, load_network_config


def test_empty_company_boards_file_is_valid(tmp_path: Path) -> None:
    (tmp_path / "company_boards.yaml").write_text("boards: []\n", encoding="utf-8")

    loaded = load_company_boards(tmp_path)

    assert loaded.boards == []
    assert loaded.enabled_boards() == []


def test_company_board_duplicate_key_fails(tmp_path: Path) -> None:
    (tmp_path / "company_boards.yaml").write_text(
        """
boards:
  - key: repetida
    company_name: Empresa A
    collector: greenhouse
    board_token: a
  - key: repetida
    company_name: Empresa B
    collector: lever
    board_token: b
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicadas"):
        load_company_boards(tmp_path)


def test_company_board_unknown_collector_and_missing_token_fail(tmp_path: Path) -> None:
    (tmp_path / "company_boards.yaml").write_text(
        """
boards:
  - key: desconhecido
    company_name: Empresa A
    collector: outro
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="coletor"):
        load_company_boards(tmp_path)

    (tmp_path / "company_boards.yaml").write_text(
        """
boards:
  - key: sem-token
    company_name: Empresa A
    collector: greenhouse
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="board_token"):
        load_company_boards(tmp_path)


def test_jobposting_board_requires_url(tmp_path: Path) -> None:
    (tmp_path / "company_boards.yaml").write_text(
        """
boards:
  - key: vaga
    company_name: Empresa A
    collector: jobposting
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="url"):
        load_company_boards(tmp_path)


def test_company_boards_local_override_replaces_by_key(tmp_path: Path) -> None:
    (tmp_path / "company_boards.yaml").write_text(
        """
boards:
  - key: empresa
    company_name: Empresa A
    collector: greenhouse
    board_token: a
    enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "company_boards.local.yaml").write_text(
        """
boards:
  - key: empresa
    company_name: Empresa A
    collector: greenhouse
    board_token: a-local
    enabled: true
    tags: [Remote]
""",
        encoding="utf-8",
    )

    loaded = load_company_boards(tmp_path)

    assert len(loaded.boards) == 1
    assert loaded.boards[0].board_token == "a-local"
    assert loaded.boards[0].enabled is True
    assert loaded.boards[0].tags == ["remote"]


def test_network_config_defaults_and_validation(tmp_path: Path) -> None:
    (tmp_path / "network.yaml").write_text(
        """
http:
  allowed_ports: [443, 80, 443]
collection:
  close_after_missing_successful_runs: 3
""",
        encoding="utf-8",
    )

    loaded = load_network_config(tmp_path)

    assert loaded.http.allowed_ports == [80, 443]
    assert loaded.collection.close_after_missing_successful_runs == 3
