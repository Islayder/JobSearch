from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urlsplit

from radar_vagas.collection.contracts import CollectionContext, CollectionResult, CollectorError
from radar_vagas.collectors.common import as_text, is_public_http_url_syntax
from radar_vagas.collectors.gupy.mapping import map_gupy_job
from radar_vagas.http.errors import HttpBudgetExceededError
from radar_vagas.ingestion.import_schema import ImportedPosting

PUBLIC_PORTAL_HOST = "employability-portal.gupy.io"
PUBLIC_PORTAL_PATH = "/api/v1/jobs"
PUBLIC_PORTAL_URL = f"https://{PUBLIC_PORTAL_HOST}{PUBLIC_PORTAL_PATH}"
PAGE_SIZE = 50


class GupyCollector:
    slug = "gupy"

    def collect(self, context: CollectionContext) -> CollectionResult:
        if context.http_client is None:
            raise CollectorError("Cliente HTTP nao configurado para coleta.")
        if context.query_mode != "public_portal":
            raise CollectorError("Gupy suporta apenas o modo public_portal neste marco.")
        search_text = as_text(context.query_parameters.get("search_text"))
        if not search_text:
            raise CollectorError("search_text e obrigatorio para consulta Gupy.")
        filters = context.query_parameters.get("filters")
        if filters is None:
            filters = {}
        if not isinstance(filters, dict):
            raise CollectorError("filters da consulta Gupy deve ser um objeto.")
        unsupported = sorted(set(filters) - {"country"})
        if unsupported:
            joined = ", ".join(unsupported)
            raise CollectorError(f"Filtros nao suportados para Gupy: {joined}.")

        max_pages = _positive_limit(context.max_pages, "max_pages")
        max_items_value = (
            context.max_items
            if context.max_items is not None
            else context.collection_config.default_max_items
        )
        max_items = _positive_limit(max_items_value, "max_items")
        items: list[ImportedPosting] = []
        invalid_items: list[dict[str, object]] = []
        warnings: list[str] = []
        recoverable_errors: list[str] = []
        requests = 0
        bytes_received = 0
        retries = 0
        pages_requested = 0
        raw_results = 0
        total_available: int | None = None
        offset = 0
        truncated = False
        repeated_page = False
        budget_limited_by: str | None = None
        seen_page_signatures: set[tuple[str, ...]] = set()
        seen_provider_keys: set[str] = set()

        for page_number in range(1, max_pages + 1):
            remaining = max_items - len(items)
            if remaining <= 0:
                truncated = True
                break
            limit = min(PAGE_SIZE, remaining)
            url = _portal_url(search_text=search_text, limit=limit, offset=offset)
            try:
                response = context.http_client.get(url, allowed_hosts=(PUBLIC_PORTAL_HOST,))
            except HttpBudgetExceededError as exc:
                requests += exc.requests_made
                retries += exc.retries
                truncated = True
                budget_limited_by = exc.limited_by
                warnings.append(f"Consulta interrompida pelo orcamento: {exc.limited_by}.")
                break
            requests += response.requests_made
            bytes_received += response.bytes_received
            retries += response.retries
            pages_requested += 1
            _raise_if_unexpected_host(response.url)

            payload = _json_payload(response.text)
            raw_jobs = payload.get("data")
            pagination = payload.get("pagination")
            if not isinstance(raw_jobs, list) or not isinstance(pagination, dict):
                raise CollectorError("Resposta Gupy nao contem data e pagination validos.")
            raw_results += len(raw_jobs)
            total_available = _int_or_none(pagination.get("total"))
            page_limit = _int_or_none(pagination.get("limit")) or limit

            page_signature = tuple(
                as_text(job.get("id")) or "" for job in raw_jobs if isinstance(job, dict)
            )
            if page_signature and page_signature in seen_page_signatures:
                repeated_page = True
                truncated = True
                warnings.append("Pagina repetida detectada; coleta interrompida como parcial.")
                break
            seen_page_signatures.add(page_signature)

            if not raw_jobs:
                break

            for page_index, job in enumerate(raw_jobs, start=1):
                if not isinstance(job, dict):
                    invalid_items.append(
                        _invalid_item(
                            offset + page_index, ["item nao e objeto"], {"type": type(job).__name__}
                        )
                    )
                    continue
                errors = _validation_errors(job)
                if errors:
                    invalid_items.append(
                        _invalid_item(offset + page_index, errors, _raw_excerpt(job))
                    )
                    continue
                if not _matches_local_filters(job, filters):
                    continue
                provider_key = f"gupy:{as_text(job.get('id'))}"
                if provider_key in seen_provider_keys:
                    continue
                seen_provider_keys.add(provider_key)
                items.append(
                    map_gupy_job(
                        job,
                        source_name=context.source_name,
                        page_number=page_number,
                        position_in_results=offset + page_index,
                    )
                )
                if len(items) >= max_items:
                    break

            offset += len(raw_jobs)
            if total_available is not None and offset >= total_available:
                break
            if len(raw_jobs) < page_limit:
                break
        else:
            if total_available is None or offset < total_available:
                truncated = True

        if invalid_items:
            warnings.append(f"Itens invalidos ignorados: {len(invalid_items)}.")
        if truncated:
            warnings.append("Resultado truncado pelos limites configurados.")

        partial = truncated or bool(invalid_items) or repeated_page
        return CollectionResult(
            collector=self.slug,
            items=items,
            requests=requests,
            bytes_received=bytes_received,
            warnings=warnings,
            recoverable_errors=recoverable_errors,
            invalid_items=invalid_items,
            metadata={
                "mode": "public_portal",
                "host": PUBLIC_PORTAL_HOST,
                "path": PUBLIC_PORTAL_PATH,
                "search_text": search_text,
                "filters": filters,
                "raw_results": raw_results,
                "processed_items": len(items),
                "invalid_items": len(invalid_items),
                "pages": pages_requested,
                "page_size": PAGE_SIZE,
                "total_available": total_available,
                "truncated": truncated,
                "repeated_page": repeated_page,
                "budget_limited_by": budget_limited_by,
                "retries": retries,
                "hydrate_details": False,
                "public_interface": "portal_public_get",
            },
            complete_snapshot=False,
            partial=partial,
            status_code=200,
        )


def _portal_url(*, search_text: str, limit: int, offset: int) -> str:
    query = urlencode({"jobName": search_text, "limit": str(limit), "offset": str(offset)})
    return f"{PUBLIC_PORTAL_URL}?{query}"


def _positive_limit(value: int | None, name: str) -> int:
    if value is None or value <= 0:
        raise CollectorError(f"{name} deve ser um inteiro positivo.")
    return value


def _json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectorError("Gupy retornou JSON invalido.") from exc
    if not isinstance(payload, dict):
        raise CollectorError("Resposta Gupy deve ser um objeto JSON.")
    return payload


def _validation_errors(job: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not as_text(job.get("id")):
        errors.append("identificador ausente")
    if not as_text(job.get("name")):
        errors.append("titulo ausente")
    public_url = as_text(job.get("jobUrl"))
    if not is_public_http_url_syntax(public_url) or not _is_public_gupy_url(public_url):
        errors.append("url publica Gupy ausente ou invalida")
    return errors


def _matches_local_filters(job: dict[str, Any], filters: dict[str, Any]) -> bool:
    country = as_text(filters.get("country"))
    return not (country and (as_text(job.get("country")) or "").casefold() != country.casefold())


def _invalid_item(
    item_index: int,
    errors: list[str],
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
        "name": as_text(job.get("name")),
        "jobUrl": as_text(job.get("jobUrl")),
        "careerPageName": as_text(job.get("careerPageName")),
    }


def _is_public_gupy_url(url: str | None) -> bool:
    if not url:
        return False
    host = (urlsplit(url).hostname or "").strip(".").lower()
    return host == "gupy.io" or host.endswith(".gupy.io")


def _raise_if_unexpected_host(url: str) -> None:
    host = (urlsplit(url).hostname or "").strip(".").lower()
    if host != PUBLIC_PORTAL_HOST:
        raise CollectorError(f"Redirect Gupy para host nao permitido: {host}.")


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
