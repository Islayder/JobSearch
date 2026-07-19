# Arquitetura

## Decisao Principal

Radar de Vagas e um monolito modular em Python. A aplicacao roda localmente, usa
SQLite como banco e separa camadas por responsabilidade:

- CLI: entrada e apresentacao no terminal.
- Configuracao: leitura e validacao de YAML e variaveis de ambiente.
- HTTP: cliente central com GET/HEAD, timeouts, retry, redirects, cache e SSRF.
- Coletores: JobPosting JSON-LD, Greenhouse, Lever e Gupy Public Portal.
- Ingestao: importacao de fixture, arquivos JSON/CSV e DTOs coletados.
- Canonicalizacao: normalizacao de textos, URLs, empresas e localidades.
- Deduplicacao: deteccao exata de publicacoes e candidatos provaveis.
- Elegibilidade: regras puras e testaveis.
- Relevancia: classificacao deterministica de area profissional.
- Ranking: pontuacao deterministica e explicavel.
- Perfil profissional: importacao local versionada e evidencias.
- Compatibilidade: comparacao explicavel entre vaga e curriculo.
- Revisao e candidaturas: politica central de estados, fila manual, guardas,
  historico local e redutor de timeline.
- Agenda local: prazos, entrevistas, testes e follow-ups sem calendario externo.
- Interface web local: FastAPI, Jinja2 e HTML/CSS server-side para operar o
  mesmo banco local pelo navegador.
- Persistencia: SQLAlchemy, sessoes, modelos e migracoes Alembic.

```mermaid
flowchart LR
    L["JSON/CSV local"] --> C["Ingestao"]
    B["Coletores publicos"] --> H["HTTP seguro"]
    H --> C
    C --> D["Normalizacao"]
    D --> E["Deduplicacao"]
    E --> F["SQLite"]
    F --> G["Elegibilidade"]
    G --> R["Ranking"]
    F --> P["Perfil profissional"]
    P --> X["Compatibilidade vaga-curriculo"]
    X --> V["Revisao manual"]
    R --> V
    V --> A["Agenda local"]
    V --> I["CLI"]
    V --> W["Web local"]
    A --> I
    A --> W
    F --> I
    F --> W
```

## Dependencias Permitidas

O projeto usa Python 3.12+, SQLAlchemy 2.x, Alembic, Pydantic 2.x, Typer, Rich,
PyYAML, httpx, beautifulsoup4, pytest, Ruff e mypy. A interface web e extra
opcional e usa FastAPI, Uvicorn, Jinja2, python-multipart e itsdangerous. A CLI
e o nucleo continuam funcionando sem instalar o extra `web`. Nao ha Django,
Flask, Streamlit, PostgreSQL, Redis, Celery ou Docker obrigatorio.

## Fluxo

1. `radar init-db` cria diretorios e aplica migracoes.
2. `radar validate-file` simula importacoes JSON/CSV sem escrita.
3. `radar import-file` valida, gera relatorio opcional, cria fontes, empresas,
   publicacoes, auditoria de origem e vagas canonicas quando seguro.
4. `radar import-url` coleta uma unica pagina publica com JSON-LD `JobPosting`.
5. `radar collect-board` coleta um board publico Greenhouse ou Lever.
6. `radar collect-all` coleta os boards ativos configurados.
7. `radar collect-query` e `radar collect-search-plan` executam consultas de
   descoberta, como buscas publicas Gupy.
8. O orquestrador registra `SourceRun`, isola a coleta por escopo estavel de
   board, atualiza publicacoes conhecidas, cria revisoes quando conteudo muda e
   fecha publicacoes ausentes somente quando a autoridade da coleta permite.
9. A relevancia usa uma entrada canonica compartilhada por dry-run, importacao,
   coleta persistida e reavaliacao de vagas existentes.
10. `radar review-queue`, `radar mark-applied`, `radar applications` e
   `radar application-event` cuidam do fluxo manual de revisao e historico,
   usando uma politica central para impedir transicoes contraditorias.
11. `radar import-profile`, `radar compare-profile` e
   `radar show-compatibility` cuidam do perfil profissional versionado e de
   comparacoes historicas auditaveis por hash da vaga.
12. `radar agenda` e comandos `*-agenda-event` cuidam da agenda local, sem
   Google Calendar, notificacoes ou leitura de e-mail.
13. `radar web` inicia uma interface local em `127.0.0.1`, aplica migracoes
   antes de servir paginas e reutiliza os mesmos servicos de dominio da CLI.
14. `radar evaluate-all`, `radar reevaluate-jobs`, `radar list-jobs`,
   `radar show-job`, `radar stats`, `radar boards` e `radar source-health`
   consultam ou atualizam o banco.

## Autoridade da Coleta

Toda coleta tem autoridade explicita:

- `AUTHORITATIVE_BOARD`: boards completos podem incrementar ausencias quando a
  execucao e bem-sucedida, completa, nao parcial, nao truncada e sem itens
  invalidos que comprometam completude.
- `DISCOVERY_QUERY`: buscas globais ou filtradas apenas observam resultados.
  Nunca fecham publicacoes ou vagas canonicas por ausencia.
- `SINGLE_PAGE`: paginas individuais nao fecham outras publicacoes.

Quando uma consulta de descoberta encontra uma publicacao ja pertencente a outro
escopo autoritativo, ela registra a observacao e o `DiscoveryHit`, mas nao muda
`collection_scope_key`, `source_id`, `source_run_id`, `missing_count`,
`is_active`, `closed_reason` ou `last_seen_at` autoritativos.

## Controle de Rede

O cliente HTTP central aplica intervalo minimo por host com relogio monotonic.
`collect-search-plan` tambem usa um orcamento global de requisicoes, itens e
duracao, compartilhado por todas as consultas do plano.

## Rollback

Na importacao generica, itens invalidos sao separados antes da escrita. Para os
itens validos, a unidade de rollback e o arquivo inteiro. Na coleta publica, uma
falha controlada registra a execucao como falha e nao incrementa ausencias nem
fecha publicacoes. Snapshots parciais por truncamento, itens invalidos ou HTTP
304 seguem a mesma regra de nao fechamento.

## Decisoes Adiadas

- Busca global por empresas.
- Crawling recursivo ou busca no Google.
- LinkedIn, Indeed, Glassdoor, Solides e Pandape.
- Geracao de curriculo.
- Gmail.
- Google Calendar.
- Interpretacao semantica ampla ou IA.
- Playwright.
- Candidatura automatica ou envio de formulario.
- Interface web publica, multiusuario ou hospedada.

## Por Que SQLite

SQLite atende ao escopo local-first, reduz configuracao, facilita testes
isolados e evita infraestrutura externa. Microservicos foram evitados porque a
primeira versao precisa de coesao, portabilidade e simplicidade operacional.
