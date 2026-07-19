# Estrategia de Fontes

## Fonte de Descoberta e Fonte Original

Uma fonte de descoberta indica onde a vaga foi encontrada. A fonte original pode
ser o ATS ou pagina de carreira onde a candidatura realmente acontece, como
Greenhouse ou Lever.

`Posting` preserva a publicacao encontrada. `Job` guarda a oportunidade canonica
quando a associacao e segura.

## Fontes Implementadas

- `jobposting`: uma pagina publica com JSON-LD `JobPosting`.
- `greenhouse`: endpoint publico de board Greenhouse.
- `lever`: Postings API publica do Lever.

Nao ha LinkedIn, Indeed, Glassdoor, Gupy, Gmail, Playwright, login, cookies,
CAPTCHA, proxy, POST de candidatura ou envio de formulario.

## Resolucao Canonica

A normalizacao usa nome de empresa, titulo, cidade, modalidade, URL e hash de
conteudo. Duplicatas exatas de publicacao sao ignoradas. Duplicatas provaveis
entre fontes nao sao unidas automaticamente e ficam disponiveis para revisao
futura.

## Coleta Incremental

Cada coleta persistida registra `SourceRun`.

Quando uma publicacao conhecida aparece igual:

- nao cria duplicata;
- atualiza `last_seen_at` e `source_run_id`;
- zera ausencias;
- nao recalcula ranking sem necessidade.

Quando uma publicacao conhecida muda:

- cria `PostingRevision`;
- atualiza os dados brutos;
- atualiza a vaga canonica quando ela nao esta protegida por candidatura ou
  descarte humano;
- reexecuta elegibilidade e ranking quando permitido.

Quando um item some de snapshot completo bem-sucedido:

- incrementa `missing_count`;
- nao fecha na primeira ausencia;
- fecha somente apos o limite configurado em `network.yaml`;
- nao incrementa ausencias em falha, timeout, execucao parcial ou HTTP 304.

Quando um item fechado reaparece:

- reabre a publicacao;
- zera ausencias;
- preserva historico e candidatura existente.

## Cache HTTP

Boards persistidos armazenam `ETag` e `Last-Modified` quando o servidor envia
esses headers. Coletas seguintes enviam `If-None-Match` e `If-Modified-Since`.
Resposta `304 Not Modified` e registrada como sucesso, sem reprocessamento e sem
fechamento de publicacoes.

## Ganho Incremental por Fonte

Cada fonte mede itens encontrados, criados, conhecidos, alterados, duplicados e
fechados para avaliar se aumenta o conjunto de vagas uteis ou apenas repete
oportunidades ja conhecidas.
