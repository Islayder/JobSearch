# Modelo de Dados

## Visao Geral

`Posting` representa uma publicacao encontrada em uma fonte. `Job` representa a
oportunidade canonica. Uma vaga real pode aparecer em varias publicacoes, mas a
uniao automatica so acontece quando for exata e segura.

```mermaid
erDiagram
    SOURCE ||--o{ SOURCE_RUN : runs
    SOURCE ||--o{ POSTING : publishes
    SOURCE ||--o{ COMPANY_BOARD : lists
    SOURCE_RUN ||--o{ POSTING : last_seen_in
    SOURCE_RUN ||--o{ POSTING_REVISION : observed
    COMPANY ||--o{ COMPANY_ALIAS : has
    COMPANY ||--o{ COMPANY_BOARD : owns
    COMPANY ||--o{ JOB : offers
    JOB ||--o{ POSTING : groups
    POSTING ||--o{ POSTING_REVISION : changed_by
    JOB ||--o| DECISION : evaluated_by
    JOB ||--o{ APPLICATION : applied_to
    APPLICATION ||--o{ APPLICATION_EVENT : has
    FILE_IMPORT_BATCH ||--o{ IMPORT_ITEM_AUDIT : audits
    POSTING ||--o{ IMPORT_ITEM_AUDIT : traced_by
```

## Entidades

`Source` guarda portais, ATS, alertas e importacoes manuais. `SourceRun`
representa uma execucao de ingestao ou coleta com inicio, fim, status e
contadores de itens.

`Company` guarda a organizacao canonica. `CompanyAlias` guarda variacoes
normalizadas do nome.

`CompanyBoard` mapeia boards publicos configurados. Campos principais:

- `key`
- `collector_type`
- `external_identifier`
- `board_url`
- `configuration_json`
- `is_active`
- `last_checked_at`
- `last_success_at`
- `last_failed_at`
- `consecutive_failures`
- `last_etag`
- `last_modified`
- `last_complete_snapshot_at`
- `last_run_id`
- `disabled_reason`

`Posting` guarda dados brutos da publicacao, URL normalizada, hash de conteudo,
fonte e associacao opcional a `Job`. Para coleta incremental, tambem guarda:

- `is_active`
- `missing_count`
- `closed_reason`

`PostingRevision` registra mudancas observadas em uma publicacao conhecida:

- hash anterior
- novo hash
- campos alterados em JSON
- data de observacao
- `SourceRun` em que a mudanca foi vista

O HTML integral de respostas externas nao e duplicado em revisoes.

`Job` guarda a vaga canonica com tipo, modalidade, localidade, remuneracao,
status e campos minimos para futura compatibilidade academica.

`Decision` guarda a ultima avaliacao de elegibilidade, motivo, nota e
detalhamento.

`Application` e `ApplicationEvent` registram candidatura e evolucao do processo,
sem envio automatico nesta etapa.

`Resume` e `ResumeVersion` guardam estrutura para futuras versoes de curriculo,
sem geracao de arquivo.

`EmailMessage` guarda estrutura para futura integracao de e-mails, sem conexao
com Gmail nesta etapa.

`FileImportBatch` e `ImportItemAudit` registram auditoria de importacoes locais.

## Chaves e Indices

Publicacoes evitam duplicidade por fonte e identificador externo, fonte e URL
normalizada, e hash de conteudo. Consultas frequentes usam indices por status,
atividade, ausencias, tipo, modalidade, cidade, empresa e nota.

`CompanyBoard.key` e unico quando presente. Boards antigos sem key podem ser
migrados e atualizados posteriormente.
