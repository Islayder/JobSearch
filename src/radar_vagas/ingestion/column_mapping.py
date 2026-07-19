from dataclasses import dataclass

from radar_vagas.canonicalization.normalize import normalize_text

CANONICAL_FIELDS = {
    "source_name",
    "source_type",
    "external_id",
    "url",
    "title",
    "company",
    "location",
    "description",
    "published_at",
    "employment_type",
    "work_model",
    "country",
    "state",
    "city",
    "remote_country_scope",
    "hours_per_day",
    "hours_per_week",
    "salary_min",
    "salary_max",
    "salary_period",
    "currency",
    "benefits",
    "application_url",
    "metadata",
}

ALIASES = {
    "source": "source_name",
    "fonte": "source_name",
    "job_title": "title",
    "cargo": "title",
    "vaga": "title",
    "empresa": "company",
    "organization": "company",
    "localizacao": "location",
    "link": "url",
    "job_url": "url",
    "modalidade": "work_model",
    "tipo_trabalho": "work_model",
    "tipo_vaga": "employment_type",
    "senioridade": "employment_type",
    "cidade": "city",
    "estado": "state",
    "pais": "country",
    "bolsa": "salary_min",
    "salario": "salary_min",
    "beneficios": "benefits",
    "descricao": "description",
}


@dataclass(frozen=True)
class MappedRow:
    fields: dict[str, object]
    metadata: dict[str, object]
    errors: list[str]


def canonical_field_name(column_name: str) -> str | None:
    normalized = normalize_text(column_name).replace(" ", "_")
    if normalized in CANONICAL_FIELDS:
        return normalized
    return ALIASES.get(normalized)


def map_row(raw_row: dict[str, str | None]) -> MappedRow:
    fields: dict[str, object] = {}
    metadata: dict[str, object] = {}
    errors: list[str] = []

    for column_name, value in raw_row.items():
        clean_value = _empty_to_none(value)
        field_name = canonical_field_name(column_name)
        if field_name is None:
            if clean_value is not None:
                metadata[column_name] = clean_value
            continue
        previous = fields.get(field_name)
        if previous is not None and clean_value is not None and previous != clean_value:
            errors.append(
                f"colunas conflitantes para '{field_name}': valor anterior '{previous}' "
                f"e valor em '{column_name}' '{clean_value}'"
            )
            continue
        if clean_value is not None:
            fields[field_name] = clean_value

    if metadata:
        existing_metadata = fields.get("metadata")
        if isinstance(existing_metadata, dict):
            fields["metadata"] = {**metadata, **existing_metadata}
        elif existing_metadata is None:
            fields["metadata"] = metadata
        else:
            errors.append("metadata explícito conflita com colunas extras")

    return MappedRow(fields=fields, metadata=metadata, errors=errors)


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
