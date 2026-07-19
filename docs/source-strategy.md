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

Para boards publicos, a identidade incremental nao depende somente do nome da
empresa. O escopo usa coletor e `key`, `board_token` ou URL. Assim dois boards
Greenhouse ou Lever da mesma empresa nao incrementam ausencias nem fecham vagas
um do outro, e uma mudanca de nome exibido da empresa nao cria uma nova fonte
quando o token/key permanece igual.

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
- preserva `last_seen_at` como a ultima execucao em que a publicacao apareceu;
- nao fecha na primeira ausencia;
- fecha somente apos o limite configurado em `network.yaml`;
- nao incrementa ausencias em falha, timeout, execucao parcial, truncamento,
  item invalido ou HTTP 304.

Quando um item fechado reaparece:

- reabre a publicacao;
- zera ausencias;
- preserva historico e candidatura existente;
- reexecuta elegibilidade e ranking quando nao houver candidatura, aplicacao ou
  descarte humano protegendo a vaga.

## Cache HTTP

Boards persistidos armazenam `ETag` e `Last-Modified` quando o servidor envia
esses headers. Coletas seguintes enviam `If-None-Match` e `If-Modified-Since`.
Resposta `304 Not Modified` e registrada como sucesso, sem reprocessamento e sem
fechamento de publicacoes.

## Ganho Incremental por Fonte

Cada fonte mede itens encontrados, criados, conhecidos, alterados, duplicados e
fechados para avaliar se aumenta o conjunto de vagas uteis ou apenas repete
oportunidades ja conhecidas.
