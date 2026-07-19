from __future__ import annotations

import json

from radar_vagas.collection.contracts import CollectionContext, CollectionResult, CollectorError
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
        max_items = context.max_items or context.collection_config.default_max_items
        items = [
            map_lever_posting(
                item,
                company_name=context.company_name,
                board_token=context.board_token,
            )
            for item in payload[:max_items]
            if isinstance(item, dict)
        ]
        warnings = []
        if len(payload) > max_items:
            warnings.append(f"Limite aplicado: {max_items} de {len(payload)} vagas.")
        return CollectionResult(
            collector=self.slug,
            items=items,
            requests=response.requests_made,
            bytes_received=response.bytes_received,
            warnings=warnings,
            metadata={"board_token": context.board_token, "raw_items": len(payload)},
            complete_snapshot=True,
            status_code=response.status_code,
            cache_etag=response.etag,
            cache_last_modified=response.last_modified,
        )
