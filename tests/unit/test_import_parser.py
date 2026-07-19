from pathlib import Path

import pytest

from radar_vagas.domain.enums import EmploymentType, WorkModel
from radar_vagas.ingestion.file_parser import parse_csv_import_file, parse_json_import_file


def test_parse_json_direct_list(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text(
        '[{"source_name": "Manual", "title": "Estágio em Dados", "company": "Empresa X"}]',
        encoding="utf-8",
    )

    parsed = parse_json_import_file(path)

    assert len(parsed.valid_items) == 1
    assert parsed.valid_items[0].posting is not None
    assert parsed.valid_items[0].posting.title == "Estágio em Dados"


def test_parse_json_envelope_and_incompatible_version(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text(
        '{"schema_version": "1.0", "items": [{"source_name": "Manual", '
        '"title": "Trainee", "company": "Empresa X"}]}',
        encoding="utf-8",
    )
    assert len(parse_json_import_file(path).valid_items) == 1

    path.write_text('{"schema_version": "9.9", "items": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema incompatível"):
        parse_json_import_file(path)


def test_json_invalid_and_partial_processing(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text(
        '[{"source_name": "Manual", "title": "Estágio", "company": "Empresa"}, 7, {}]',
        encoding="utf-8",
    )

    parsed = parse_json_import_file(path)

    assert len(parsed.valid_items) == 1
    assert len(parsed.invalid_items) == 2

    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON inválido"):
        parse_json_import_file(path)


def test_csv_comma_aliases_and_extra_metadata(tmp_path: Path) -> None:
    path = tmp_path / "jobs.csv"
    path.write_text(
        "fonte,cargo,empresa,modalidade,tipo_vaga,campo_extra\n"
        "Manual,Analista Junior,Empresa X,home office,jr,valor\n",
        encoding="utf-8",
    )

    parsed = parse_csv_import_file(path)
    posting = parsed.valid_items[0].posting

    assert posting is not None
    assert posting.source_name == "Manual"
    assert posting.work_model is WorkModel.REMOTE
    assert posting.employment_type is EmploymentType.JUNIOR
    assert posting.metadata["campo_extra"] == "valor"


def test_csv_semicolon_bom_empty_fields_and_benefits(tmp_path: Path) -> None:
    path = tmp_path / "jobs.csv"
    path.write_text(
        "\ufefffonte;cargo;empresa;benefícios;link;bolsa\n"
        'Manual;Estágio;Empresa X;"vale refeição;auxílio internet";;1400\n',
        encoding="utf-8",
    )

    parsed = parse_csv_import_file(path, delimiter=";")
    posting = parsed.valid_items[0].posting

    assert posting is not None
    assert posting.url is None
    assert posting.salary_min == 1400
    assert posting.benefits == ["vale refeição", "auxílio internet"]


def test_csv_pipe_benefits_and_conflicting_aliases(tmp_path: Path) -> None:
    path = tmp_path / "jobs.csv"
    path.write_text(
        "source,fonte,cargo,empresa,beneficios\n"
        "Manual A,Manual B,Estágio,Empresa X,vale|transporte\n",
        encoding="utf-8",
    )

    parsed = parse_csv_import_file(path)

    assert len(parsed.invalid_items) == 1
    assert "conflitantes" in parsed.invalid_items[0].errors[0]
