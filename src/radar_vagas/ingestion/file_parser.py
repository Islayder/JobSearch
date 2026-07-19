import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from radar_vagas.ingestion.column_mapping import map_row
from radar_vagas.ingestion.import_schema import ImportedPosting

SUPPORTED_SCHEMA_VERSIONS = {"1.0"}


@dataclass(frozen=True)
class ParsedImportItem:
    line_number: int | None
    item_index: int
    raw_fields: dict[str, Any]
    posting: ImportedPosting | None
    errors: list[str]

    @property
    def is_valid(self) -> bool:
        return self.posting is not None and not self.errors


@dataclass(frozen=True)
class ParsedImportFile:
    input_file: Path
    file_format: str
    schema_version: str
    items: list[ParsedImportItem]

    @property
    def valid_items(self) -> list[ParsedImportItem]:
        return [item for item in self.items if item.is_valid]

    @property
    def invalid_items(self) -> list[ParsedImportItem]:
        return [item for item in self.items if not item.is_valid]


def parse_import_file(path: Path, *, delimiter: str | None = None) -> ParsedImportFile:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_json_import_file(path)
    if suffix == ".csv":
        return parse_csv_import_file(path, delimiter=delimiter)
    raise ValueError("Formato não suportado. Use .json ou .csv.")


def parse_json_import_file(path: Path) -> ParsedImportFile:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido: {exc.msg}") from exc

    schema_version = "1.0"
    raw_items: object
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        schema_version = str(payload.get("schema_version", "1.0"))
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Versão de schema incompatível: {schema_version}")
        raw_items = payload.get("items")
    else:
        raise ValueError("JSON deve ser uma lista ou envelope com items.")

    if not isinstance(raw_items, list):
        raise ValueError("JSON não contém lista válida de items.")

    parsed_items: list[ParsedImportItem] = []
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            parsed_items.append(
                ParsedImportItem(
                    line_number=None,
                    item_index=index,
                    raw_fields={"value": raw_item},
                    posting=None,
                    errors=["item deve ser um objeto"],
                )
            )
            continue
        parsed_items.append(_parse_item(raw_item, item_index=index, line_number=None))

    return ParsedImportFile(
        input_file=path,
        file_format="json",
        schema_version=schema_version,
        items=parsed_items,
    )


def parse_csv_import_file(path: Path, *, delimiter: str | None = None) -> ParsedImportFile:
    content = path.read_text(encoding="utf-8-sig")
    detected_delimiter = delimiter or _detect_delimiter(content)
    if detected_delimiter not in {",", ";"}:
        raise ValueError("Delimitador inválido. Use vírgula ou ponto e vírgula.")

    reader = csv.DictReader(content.splitlines(), delimiter=detected_delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV sem cabeçalho.")

    parsed_items: list[ParsedImportItem] = []
    for index, row in enumerate(reader, start=1):
        mapped = map_row(row)
        parsed_items.append(
            _parse_item(
                mapped.fields,
                item_index=index,
                line_number=index + 1,
                raw_fields={key: value for key, value in row.items() if key is not None},
                pre_errors=mapped.errors,
            )
        )

    return ParsedImportFile(
        input_file=path,
        file_format="csv",
        schema_version="1.0",
        items=parsed_items,
    )


def _parse_item(
    fields: dict[str, Any],
    *,
    item_index: int,
    line_number: int | None,
    raw_fields: dict[str, Any] | None = None,
    pre_errors: list[str] | None = None,
) -> ParsedImportItem:
    errors = list(pre_errors or [])
    try:
        posting = ImportedPosting.model_validate(fields)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        posting = None
        errors.append(_error_text(exc))

    return ParsedImportItem(
        line_number=line_number,
        item_index=item_index,
        raw_fields=raw_fields or fields,
        posting=posting,
        errors=errors,
    )


def _detect_delimiter(content: str) -> str:
    first_line = content.splitlines()[0] if content.splitlines() else ""
    comma_count = first_line.count(",")
    semicolon_count = first_line.count(";")
    return ";" if semicolon_count > comma_count else ","


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)
