from __future__ import annotations

import json

from radar_vagas.collection.contracts import CollectionContext, CollectionResult, CollectorError
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
        max_items = context.max_items or context.collection_config.default_max_items
        items = [
            map_greenhouse_job(
                job,
                company_name=context.company_name,
                board_token=context.board_token,
            )
            for job in raw_jobs[:max_items]
            if isinstance(job, dict)
        ]
        warnings = []
        if len(raw_jobs) > max_items:
            warnings.append(f"Limite aplicado: {max_items} de {len(raw_jobs)} vagas.")
        return CollectionResult(
            collector=self.slug,
            items=items,
            requests=response.requests_made,
            bytes_received=response.bytes_received,
            warnings=warnings,
            metadata={"board_token": context.board_token, "raw_items": len(raw_jobs)},
            complete_snapshot=True,
            status_code=response.status_code,
            cache_etag=response.etag,
            cache_last_modified=response.last_modified,
        )
