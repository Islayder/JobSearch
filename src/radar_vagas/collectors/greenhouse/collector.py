from __future__ import annotations

import json
from typing import Any

from radar_vagas.collection.contracts import CollectionContext, CollectionResult, CollectorError
from radar_vagas.collectors.common import as_text, is_public_http_url_syntax
from radar_vagas.collectors.greenhouse.mapping import map_greenhouse_job
from radar_vagas.http.client import cache_request_headers


class GreenhouseCollector:
    slug = "greenhouse"

    def collect(self, context: CollectionContext) -> CollectionResult:
        if context.http_client is None:
            raise CollectorError("Cliente HTTP nao configurado para coleta.")
        if not context.board_token:
            raise CollectorError("board_token e obrigatorio para Greenhouse.")
        if not context.company_name:
            raise CollectorError("company e obrigatorio para Greenhouse.")
        url = f"https://boards-api.greenhouse.io/v1/boards/{context.board_token}/jobs?content=true"
        response = context.http_client.get(
            url,
            headers=cache_request_headers(context.cache_etag, context.cache_last_modified),
        )
        if response.not_modified:
            return CollectionResult(
                collector=self.slug,
                items=[],
                requests=response.requests_made,
                bytes_received=response.bytes_received,
                complete_snapshot=True,
                not_modified=True,
                status_code=response.status_code,
                cache_etag=response.etag,
                cache_last_modified=response.last_modified,
            )
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise CollectorError("Greenhouse retornou JSON invalido.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise CollectorError("Resposta Greenhouse nao contem lista jobs.")
        raw_jobs = payload["jobs"]
        max_items = (
            context.max_items
            if context.max_items is not None
            else context.collection_config.default_max_items
        )
        if max_items <= 0:
            raise CollectorError("max_items deve ser um inteiro positivo.")
        considered_jobs = raw_jobs[:max_items]
        items = []
        invalid_items = []
        for index, job in enumerate(considered_jobs, start=1):
            if not isinstance(job, dict):
                invalid_items.append(
                    _invalid_item(
                        index,
                        ["item nao e um objeto"],
                        raw_excerpt={"type": type(job).__name__},
                    )
                )
                continue
            errors = _validation_errors(job)
            if errors:
                invalid_items.append(_invalid_item(index, errors, raw_excerpt=_raw_excerpt(job)))
                continue
            items.append(
                map_greenhouse_job(
                    job,
                    company_name=context.company_name,
                    board_token=context.board_token,
                    source_name=context.source_name,
                )
            )
        warnings = []
        truncated = len(raw_jobs) > max_items
        if truncated:
            warnings.append(f"Limite aplicado: {max_items} de {len(raw_jobs)} vagas.")
        if invalid_items:
            warnings.append(f"Itens invalidos ignorados: {len(invalid_items)}.")
        partial = truncated or bool(invalid_items)
        return CollectionResult(
            collector=self.slug,
            items=items,
            requests=response.requests_made,
            bytes_received=response.bytes_received,
            warnings=warnings,
            invalid_items=invalid_items,
            metadata={
                "board_token": context.board_token,
                "raw_items": len(raw_jobs),
                "considered_items": len(considered_jobs),
                "processed_items": len(items),
                "invalid_items": len(invalid_items),
                "truncated": truncated,
            },
            complete_snapshot=not partial,
            partial=partial,
            status_code=response.status_code,
            cache_etag=response.etag,
            cache_last_modified=response.last_modified,
        )


def _validation_errors(job: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not as_text(job.get("title")):
        errors.append("titulo ausente")
    if not as_text(job.get("id")):
        errors.append("identificador ausente")
    if not is_public_http_url_syntax(as_text(job.get("absolute_url"))):
        errors.append("url publica ausente ou invalida")
    return errors


def _invalid_item(
    item_index: int,
    errors: list[str],
    *,
    raw_excerpt: dict[str, object],
) -> dict[str, object]:
    return {
        "item_index": item_index,
        "errors": errors,
        "raw_excerpt": raw_excerpt,
    }


def _raw_excerpt(job: dict[str, Any]) -> dict[str, object]:
    return {
        "id": as_text(job.get("id")),
        "title": as_text(job.get("title")),
        "absolute_url": as_text(job.get("absolute_url")),
    }
