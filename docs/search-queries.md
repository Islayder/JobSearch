# Consultas de Descoberta

Consultas de descoberta representam buscas globais ou filtradas que nao sao
boards autoritativos. Elas ficam em `config/search_queries.yaml` e podem ser
ajustadas localmente em `config/search_queries.local.yaml`, arquivo ignorado
pelo Git.

## Configuracao

Cada consulta tem:

- `key` unica;
- `collector`;
- `mode`;
- `search_text`;
- `filters`;
- `max_pages`;
- `max_items`;
- `priority`;
- `tags`;
- `hydrate_details`.

O fingerprint e deterministico e muda quando parametros relevantes mudam. O
`collection_scope_key` permanece estavel por key para impedir que uma mudanca
de filtro feche vagas antigas.

## Autoridade

Consultas usam `DISCOVERY_QUERY`. Elas nunca:

- incrementam `missing_count`;
- fecham `Posting`;
- fecham `Job`;
- interpretam ausencia como encerramento.

Uma consulta vazia, parcial, truncada, com falha ou com pagina repetida continua
sem autoridade de fechamento.

## CLI

```powershell
radar queries
radar show-query gupy-estagio-dados
radar collect-query gupy-estagio-dados --dry-run --max-pages 1 --max-items 5
radar collect-search-plan --collector gupy --tag data --dry-run
radar query-health
```

Dry-run nao grava `Source`, `SourceRun`, `SearchQuery`, `DiscoveryHit`,
`Company`, `Posting`, `Job`, `Decision` ou revisoes.

## DiscoveryHit

`DiscoveryHit` registra que uma consulta encontrou uma publicacao em uma
execucao. Ele guarda query, run, posting, job, identidade da plataforma, pagina,
posicao e metadados resumidos. Metadados nao devem conter descricoes completas.
