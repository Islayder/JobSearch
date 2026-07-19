# Coletor Gupy

## Reconhecimento Publico Validado

Reconhecimento feito em 2026-07-19 com GET/HEAD publico, sem token, login,
cookie de sessao, cabecalho secreto, POST, CAPTCHA, proxy ou Playwright.

Paginas acessadas:

- `https://portal.gupy.io/vagas?searchTerm=dados`
- `https://vempra.gupy.io/`
- `https://softdesign.gupy.io/`

Interface publica usada pelo frontend do portal:

- host: `employability-portal.gupy.io`
- caminho: `/api/v1/jobs`
- metodo: GET
- parametros: `jobName`, `limit`, `offset`
- formato: JSON com `data` e `pagination`
- paginacao: `pagination.total`, `pagination.limit`, `pagination.offset`
- HEAD: retorna 200 e `application/json`
- limites invalidos observados: `limit=0` e `offset=-1` retornaram 400
- cookie: nenhum `Set-Cookie` observado nas respostas validadas

Campos observados no item publico: `id`, `companyId`, `name`, `description`,
`careerPageId`, `careerPageName`, `careerPageLogo`, `careerPageUrl`, `type`,
`publishedDate`, `applicationDeadline`, `isRemoteWork`, `city`, `state`,
`country`, `jobUrl`, `badges`, `workplaceType`, `disabilities`, `skills`.
Departamento, area e tecnologias sao aproveitados quando aparecem nesses campos
publicos, especialmente `careerPageName` e `skills`.

As paginas de carreira em subdominios `*.gupy.io` renderizam HTML publico com
`__NEXT_DATA__` e uma lista resumida de vagas. Esse modo nao foi implementado
neste marco porque o modo `public_portal` ja fornece campos suficientes para a
consulta de descoberta e a pagina de carreira exige outra politica de
completude.

## Diferenciacao de Interfaces

- API publica oficial autenticada: `https://api.gupy.io/api/v1/jobs`. Nao e
  usada, porque requer token autorizado.
- Interface publica usada pelo portal: `https://employability-portal.gupy.io/api/v1/jobs`.
  E usada somente por GET/HEAD sem autenticacao.
- Pagina HTML publica: `portal.gupy.io` e subdominios `*.gupy.io`. Usadas para
  reconhecimento/documentacao, nao para candidatura.

## Implementacao

O coletor `gupy` suporta `mode: public_portal` e usa autoridade
`DISCOVERY_QUERY`. Ele:

- valida `max_pages` e `max_items` como positivos;
- usa allowlist do host `employability-portal.gupy.io`;
- preserva URL publica da vaga em `*.gupy.io` sem acessa-la como candidatura;
- deduplica por `gupy:<job_id>`;
- preserva campos estruturados para relevancia reproduzivel;
- marca truncamento como parcial;
- detecta pagina repetida como parcial;
- ignora itens invalidos sem interromper a pagina inteira;
- nao fecha vagas por ausencia.

## Controle de Rede e Piloto

Consultas Gupy devem passar pelo cliente HTTP central, com allowlist do host,
intervalo minimo por host e orcamento global quando executadas por
`collect-search-plan`. Um piloto persistente deve usar limites baixos, gerar
relatorio ignorado em `data/exports/`, preservar backup local ignorado e nao
executar qualquer acao de candidatura.

Fixtures de teste sao sinteticas e nao copiam descricoes reais.
