from __future__ import annotations

import json
from typing import Any

from radar_vagas.collection.contracts import CollectionContext, CollectionResult, CollectorError
from radar_vagas.collectors.common import as_text, is_public_http_url_syntax
from radar_vagas.collectors.lever.mapping import map_lever_posting
from radar_vagas.http.client import cache_request_headers


class LeverCollector:
    slug = "lever"

    def collect(self, context: CollectionContext) -> CollectionResult:
        if context.http_client is None:
            raise CollectorError("Cliente HTTP nao configurado para coleta.")
        if not context.board_token:
            raise CollectorError("board_token e obrigatorio para Lever.")
        if not context.company_name:
            raise CollectorError("company e obrigatorio para Lever.")
        url = f"https://api.lever.co/v0/postings/{context.board_token}?mode=json"
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
            raise CollectorError("Lever retornou JSON invalido.") from exc
        if not isinstance(payload, list):
            raise CollectorError("Resposta Lever nao contem lista de postings.")
        max_items = (
            context.max_items
            if context.max_items is not None
            else context.collection_config.default_max_items
        )
        if max_items <= 0:
            raise CollectorError("max_items deve ser um inteiro positivo.")
        considered_items = payload[:max_items]
        items = []
        invalid_items = []
        for index, item in enumerate(considered_items, start=1):
            if not isinstance(item, dict):
                invalid_items.append(
                    _invalid_item(
                        index,
                        ["item nao e um objeto"],
                        raw_excerpt={"type": type(item).__name__},
                    )
                )
                continue
            errors = _validation_errors(item)
            if errors:
                invalid_items.append(_invalid_item(index, errors, raw_excerpt=_raw_excerpt(item)))
                continue
            items.append(
                map_lever_posting(
                    item,
                    company_name=context.company_name,
                    board_token=context.board_token,
                    source_name=context.source_name,
                )
            )
        warnings = []
        truncated = len(payload) > max_items
        if truncated:
            warnings.append(f"Limite aplicado: {max_items} de {len(payload)} vagas.")
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
                "raw_items": len(payload),
                "considered_items": len(considered_items),
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


def _validation_errors(posting: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not as_text(posting.get("text")):
        errors.append("titulo ausente")
    if not as_text(posting.get("id")):
        errors.append("identificador ausente")
    public_url = as_text(posting.get("hostedUrl")) or as_text(posting.get("applyUrl"))
    if not is_public_http_url_syntax(public_url):
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


def _raw_excerpt(posting: dict[str, Any]) -> dict[str, object]:
    return {
        "id": as_text(posting.get("id")),
        "text": as_text(posting.get("text")),
        "hostedUrl": as_text(posting.get("hostedUrl")),
        "applyUrl": as_text(posting.get("applyUrl")),
    }
