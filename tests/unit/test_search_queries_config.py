from __future__ import annotations

from pathlib import Path

import pytest

from radar_vagas.config.loaders import load_search_queries


def test_empty_search_queries_file_is_valid(tmp_path: Path) -> None:
    (tmp_path / "search_queries.yaml").write_text("queries: []\n", encoding="utf-8")

    loaded = load_search_queries(tmp_path)

    assert loaded.queries == []
    assert loaded.enabled_queries() == []


def test_search_query_valid_config_has_stable_scope_and_fingerprint(tmp_path: Path) -> None:
    _write_query(tmp_path, search_text="estagio dados")

    first = load_search_queries(tmp_path).queries[0]
    second = load_search_queries(tmp_path).queries[0]

    assert first.collection_scope_key == "search-query-gupy-estagio-dados"
    assert first.configuration_fingerprint == second.configuration_fingerprint
    assert first.tags == ["data", "internship"]


def test_search_query_fingerprint_changes_with_relevant_config(tmp_path: Path) -> None:
    _write_query(tmp_path, search_text="estagio dados")
    first = load_search_queries(tmp_path).queries[0]
    _write_query(tmp_path, search_text="estagio analytics")

    changed = load_search_queries(tmp_path).queries[0]

    assert changed.configuration_fingerprint != first.configuration_fingerprint
    assert changed.collection_scope_key == first.collection_scope_key


def test_search_query_duplicate_key_fails(tmp_path: Path) -> None:
    (tmp_path / "search_queries.yaml").write_text(
        """
queries:
  - key: repetida
    collector: gupy
    mode: public_portal
    search_text: dados
  - key: repetida
    collector: gupy
    mode: public_portal
    search_text: analytics
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicadas"):
        load_search_queries(tmp_path)


def test_search_query_unknown_collector_mode_limits_and_filters_fail(tmp_path: Path) -> None:
    _write_query(tmp_path, collector="outro")
    with pytest.raises(ValueError, match="coletor"):
        load_search_queries(tmp_path)

    _write_query(tmp_path, mode="career_page")
    with pytest.raises(ValueError, match="modo"):
        load_search_queries(tmp_path)

    _write_query(tmp_path, max_pages=0)
    with pytest.raises(ValueError):
        load_search_queries(tmp_path)

    _write_query(tmp_path, max_items=-1)
    with pytest.raises(ValueError):
        load_search_queries(tmp_path)

    _write_query(tmp_path, extra_filter="city")
    with pytest.raises(ValueError, match="filtros"):
        load_search_queries(tmp_path)


def test_search_query_local_override_replaces_by_key(tmp_path: Path) -> None:
    _write_query(tmp_path, search_text="estagio dados", enabled=False)
    (tmp_path / "search_queries.local.yaml").write_text(
        """
queries:
  - key: gupy-estagio-dados
    collector: gupy
    mode: public_portal
    enabled: true
    priority: 5
    tags: [Data]
    search_text: estagio dados local
    max_pages: 2
    max_items: 10
""",
        encoding="utf-8",
    )

    query = load_search_queries(tmp_path).queries[0]

    assert query.enabled is True
    assert query.priority == 5
    assert query.tags == ["data"]
    assert query.search_text == "estagio dados local"


def test_search_query_rejects_credentials(tmp_path: Path) -> None:
    _write_query(tmp_path, extra_filter="authorization")

    with pytest.raises(ValueError, match=r"credencial|filtros"):
        load_search_queries(tmp_path)


def _write_query(
    tmp_path: Path,
    *,
    collector: str = "gupy",
    mode: str = "public_portal",
    search_text: str = "estagio dados",
    enabled: bool = True,
    max_pages: int = 10,
    max_items: int = 200,
    extra_filter: str | None = None,
) -> None:
    filters = "      country: Brasil\n"
    if extra_filter is not None:
        filters += f"      {extra_filter}: valor\n"
    (tmp_path / "search_queries.yaml").write_text(
        f"""
queries:
  - key: gupy-estagio-dados
    collector: {collector}
    mode: {mode}
    enabled: {str(enabled).lower()}
    priority: 10
    tags: [Internship, Data]
    search_text: {search_text}
    filters:
{filters}    max_pages: {max_pages}
    max_items: {max_items}
""",
        encoding="utf-8",
    )
