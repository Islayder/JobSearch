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

Consultas sao donas apenas das proprias observacoes. Se encontrarem uma vaga que
ja pertence a um board autoritativo, elas nao assumem propriedade da publicacao
nem alteram o ciclo de vida autoritativo.

## Autoridade

Consultas usam `DISCOVERY_QUERY`. Elas nunca:

- incrementam `missing_count`;
- fecham `Posting`;
- fecham `Job`;
- interpretam ausencia como encerramento.

Uma consulta vazia, parcial, truncada, com falha ou com pagina repetida continua
sem autoridade de fechamento.

Quando a consulta encontra publicacao fechada ou inativa, o `DiscoveryHit` pode
ser marcado como `lifecycle_conflict`. Isso informa que a vaga foi vista pela
descoberta, mas nao autoriza reabertura.

## CLI

```powershell
radar queries
radar show-query gupy-estagio-dados
radar collect-query gupy-estagio-dados --dry-run --max-pages 1 --max-items 5
radar collect-search-plan --collector gupy --tag data --dry-run --max-total-requests 8
radar query-health
```

Dry-run nao grava `Source`, `SourceRun`, `SearchQuery`, `DiscoveryHit`,
`Company`, `Posting`, `Job`, `Decision` ou revisoes.

O mesmo executor do plano e reutilizado pela interface web para a acao manual
de coleta. A web nao chama a CLI em subprocesso e mantem apenas uma coleta em
andamento por processo local.

## DiscoveryHit

`DiscoveryHit` registra que uma consulta encontrou uma publicacao em uma
execucao. Ele guarda query, run, posting, job, identidade da plataforma, pagina,
posicao e metadados resumidos. Metadados nao devem conter descricoes completas.

## Piloto Persistente

Um piloto persistente deve ser executado somente depois dos testes offline e com
limites pequenos. Antes da execucao, faca backup do banco local ignorado pelo
Git, rode um dry-run equivalente, confirme que nao ha candidatura automatica e
entao persista a coleta. Reexecutar o mesmo plano deve ser idempotente para
publicacoes: novas duplicatas de `provider_identity_key` nao podem aparecer.
