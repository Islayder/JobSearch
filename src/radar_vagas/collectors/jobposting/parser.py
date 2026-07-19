from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from radar_vagas.collection.contracts import CollectorError


class JobPostingJsonError(CollectorError):
    """Invalid JSON-LD payload."""


class NoJobPostingError(CollectorError):
    """No JobPosting object was found in JSON-LD."""


class MultipleJobPostingsError(CollectorError):
    """Multiple JobPosting objects require an explicit selection."""


def extract_json_ld_documents(html: str) -> list[Any]:
    soup = BeautifulSoup(html, "html.parser")
    documents: list[Any] = []
    errors: list[str] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type", "")).split(";", 1)[0].strip().lower()
        if script_type != "application/ld+json":
            continue
        raw = script.string if script.string is not None else script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            documents.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            errors.append(f"JSON-LD invalido: {exc.msg}")
    if errors and not documents:
        raise JobPostingJsonError("; ".join(errors))
    return documents


def extract_jobposting_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for document in extract_json_ld_documents(html):
        objects.extend(iter_jobposting_objects(document))
    if not objects:
        raise NoJobPostingError("Nenhum objeto JobPosting encontrado no JSON-LD.")
    return objects


def select_jobposting_object(
    objects: list[dict[str, Any]],
    *,
    include_all: bool,
    selected_index: int | None,
) -> list[dict[str, Any]]:
    if include_all and selected_index is not None:
        raise CollectorError("Use --all ou --select, nao ambos.")
    if include_all:
        return objects
    if selected_index is not None:
        if selected_index < 1 or selected_index > len(objects):
            raise CollectorError(f"Selecao invalida: informe um numero entre 1 e {len(objects)}.")
        return [objects[selected_index - 1]]
    if len(objects) > 1:
        raise MultipleJobPostingsError(
            f"A pagina contem {len(objects)} objetos JobPosting. Use --all ou --select N."
        )
    return objects


def iter_jobposting_objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from iter_jobposting_objects(item)
        return
    if not isinstance(value, dict):
        return
    if _is_jobposting(value):
        yield value
    graph = value.get("@graph")
    if graph is not None:
        yield from iter_jobposting_objects(graph)


def _is_jobposting(value: dict[str, Any]) -> bool:
    raw_type = value.get("@type")
    if isinstance(raw_type, str):
        return raw_type.lower() == "jobposting"
    if isinstance(raw_type, list):
        return any(isinstance(item, str) and item.lower() == "jobposting" for item in raw_type)
    return False
