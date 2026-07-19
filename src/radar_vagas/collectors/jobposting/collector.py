from __future__ import annotations

from radar_vagas.collection.contracts import CollectionContext, CollectionResult, CollectorError
from radar_vagas.collectors.jobposting.mapping import map_jobposting_object
from radar_vagas.collectors.jobposting.parser import (
    extract_jobposting_objects,
    select_jobposting_object,
)


class JobPostingCollector:
    slug = "jobposting"

    def collect(self, context: CollectionContext) -> CollectionResult:
        if context.http_client is None:
            raise CollectorError("Cliente HTTP nao configurado para coleta.")
        if not context.url:
            raise CollectorError("URL e obrigatoria para import-url.")
        response = context.http_client.get(context.url)
        if response.not_modified:
            return CollectionResult(
                collector=self.slug,
                items=[],
                requests=response.requests_made,
                bytes_received=response.bytes_received,
                complete_snapshot=False,
                not_modified=True,
                status_code=response.status_code,
                cache_etag=response.etag,
                cache_last_modified=response.last_modified,
            )
        objects = extract_jobposting_objects(response.text)
        selected = select_jobposting_object(
            objects,
            include_all=context.include_all,
            selected_index=context.selected_index,
        )
        max_items = context.max_items or context.collection_config.default_max_items
        postings = [
            map_jobposting_object(
                item,
                page_url=response.url,
                source_name=context.source_name,
                default_company=context.company_name,
                item_index=index,
            )
            for index, item in enumerate(selected[:max_items], start=1)
        ]
        warnings = []
        if len(selected) > max_items:
            warnings.append(f"Limite aplicado: {max_items} de {len(selected)} objetos.")
        return CollectionResult(
            collector=self.slug,
            items=postings,
            requests=response.requests_made,
            bytes_received=response.bytes_received,
            warnings=warnings,
            metadata={"jobposting_objects": len(objects)},
            complete_snapshot=False,
            status_code=response.status_code,
            cache_etag=response.etag,
            cache_last_modified=response.last_modified,
        )
