# Maquinas de Estado

## Publicacao

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> LINKED: associada a Job
    NEW --> PROBABLE_DUPLICATE: duplicata provavel
    NEW --> SKIPPED_DUPLICATE: duplicata exata
    LINKED --> LINKED: vista inalterada
    LINKED --> LINKED: conteudo alterado com revisao
    LINKED --> CLOSED: ausente apos limite configurado
    CLOSED --> LINKED: reaparecimento
```

`CLOSED` em `Posting` significa que a publicacao deixou de aparecer em snapshots
completos bem-sucedidos. Falhas, snapshots parciais, payload truncado, itens
invalidos e HTTP 304 nao fecham publicacoes. Ausencia incrementa
`missing_count`, mas nao atualiza `last_seen_at`.

Consultas de descoberta (`DISCOVERY_QUERY`) e paginas individuais
(`SINGLE_PAGE`) nao fecham publicacoes. Mesmo uma consulta vazia, truncada,
parcial ou repetida apenas registra a execucao e nao interpreta ausencia como
encerramento.

Quando uma consulta de descoberta encontra uma publicacao fechada ou pertencente
a outro escopo autoritativo, ela registra a observacao sem reabrir, fechar,
zerar ausencia ou atualizar `last_seen_at` autoritativo.

## Vaga

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> PENDING_REVIEW: regra inconclusiva
    NEW --> ELIGIBLE: elegivel sem recomendacao
    NEW --> RECOMMENDED: elegivel com nota alta
    NEW --> ARCHIVED: incompativel
    PENDING_REVIEW --> ELIGIBLE: revisao aprovada
    PENDING_REVIEW --> DISMISSED: revisao descartada
    ELIGIBLE --> SEEN: visualizada
    ELIGIBLE --> DISMISSED: descartada
    RECOMMENDED --> SEEN: visualizada
    RECOMMENDED --> APPLIED: candidatura registrada
    APPLIED --> CLOSED: processo encerrado
    ARCHIVED --> [*]
    CLOSED --> ELIGIBLE: reaparecimento reavaliado
    CLOSED --> RECOMMENDED: reaparecimento reavaliado
    CLOSED --> PENDING_REVIEW: reaparecimento reavaliado
    CLOSED --> ARCHIVED: reaparecimento reavaliado
    EXPIRED --> [*]
```

Quando uma publicacao fechada reaparece, a vaga volta para avaliacao se nao
houver candidatura, aplicacao ou descarte humano protegendo o historico. Ela nao
fica parada em `NEW`: pode voltar como `ELIGIBLE`, `RECOMMENDED`,
`PENDING_REVIEW` ou `ARCHIVED`, conforme regras atuais.

`DISMISSED`, `APPLIED` e vagas com candidatura existente nao voltam ao ranking
automaticamente por causa de uma mudanca ou reaparecimento de publicacao.
Quando houver candidatura previa, a vaga passa a ser acompanhada como historico.
`radar reevaluate-jobs` segue a mesma protecao e nao sobrescreve esses estados.

Relevancia profissional afeta a transicao inicial: `UNRELATED` leva a
`ARCHIVED`, `MANUAL_REVIEW` leva a `PENDING_REVIEW`, `CORE` e `ADJACENT`
seguem elegibilidade e ranking. Incompatibilidades de empresa, localidade, tipo
e candidatura anterior prevalecem.

## Revisao Manual

```mermaid
stateDiagram-v2
    [*] --> UNREVIEWED
    UNREVIEWED --> SEEN
    UNREVIEWED --> SHORTLISTED
    UNREVIEWED --> DISMISSED
    SEEN --> SHORTLISTED
    SHORTLISTED --> SEEN
    SEEN --> DISMISSED
    SHORTLISTED --> DISMISSED
    DISMISSED --> UNREVIEWED: restore-job
    UNREVIEWED --> APPLIED
    SEEN --> APPLIED
    SHORTLISTED --> APPLIED
```

`JobReviewState` e o estado atual. `JobReviewEvent` e append-only e guarda a
origem manual da mudanca. Todas as transicoes passam pela politica central de
revisao antes de alterar `Job.status`, `JobReviewState` ou gravar evento.

Transicoes validas:

- `UNREVIEWED` para `SEEN`, `SHORTLISTED`, `DISMISSED` ou `APPLIED`.
- `SEEN` para `SHORTLISTED`, `DISMISSED` ou `APPLIED`.
- `SHORTLISTED` para `SEEN`, `DISMISSED` ou `APPLIED`.
- `DISMISSED` para `UNREVIEWED` somente via `restore-job`.

Estados `APPLIED`, `CLOSED` e `EXPIRED` bloqueiam a revisao manual comum.
Vagas com candidatura existente tambem bloqueiam `mark-seen`, `shortlist` e
`dismiss-job`; a vaga deve ser acompanhada pelo historico de candidatura.

`restore-job` limpa o descarte humano ativo, reavalia a vaga com as regras
atuais e nao restaura vagas `APPLIED`, `CLOSED` ou `EXPIRED`.

## Candidatura

```mermaid
stateDiagram-v2
    [*] --> APPLIED
    APPLIED --> AWAITING_UPDATE
    AWAITING_UPDATE --> ASSESSMENT_RECEIVED
    ASSESSMENT_RECEIVED --> ASSESSMENT_COMPLETED
    AWAITING_UPDATE --> CASE_RECEIVED
    CASE_RECEIVED --> CASE_SUBMITTED
    AWAITING_UPDATE --> INTERVIEW_SCHEDULED
    CASE_SUBMITTED --> INTERVIEW_SCHEDULED
    ASSESSMENT_COMPLETED --> INTERVIEW_SCHEDULED
    INTERVIEW_SCHEDULED --> INTERVIEW_COMPLETED
    INTERVIEW_COMPLETED --> OFFER_RECEIVED
    APPLIED --> REJECTED
    AWAITING_UPDATE --> REJECTED
    ASSESSMENT_COMPLETED --> REJECTED
    INTERVIEW_COMPLETED --> REJECTED
    APPLIED --> WITHDRAWN
    AWAITING_UPDATE --> WITHDRAWN
```

A candidatura automatica e proibida nesta versao. O estado existe para rastrear
acoes humanas feitas fora do Radar.

Eventos manuais ou importados atualizam o resumo da candidatura, mas nao abrem
links externos e nao executam candidatura em plataforma. `SUBMITTED` registra
que o usuario aplicou fora do Radar; `CONFIRMATION_RECEIVED` e eventos
posteriores acompanham o processo.

O resumo de `Application.status` e `Application.stage` e derivado por redutor de
timeline. O redutor ordena eventos por data e usa a sequencia resultante para
reconstruir a etapa atual. Eventos informativos, como confirmacao recebida e
atualizacao de processo, nao regridem uma etapa mais avancada. Um evento antigo
inserido depois nao derruba uma etapa mais recente. Eventos terminais, como
rejeicao ou retirada, podem ser substituidos por um evento posterior explicito,
por exemplo uma entrevista registrada depois de uma rejeicao importada
incorretamente.

`ApplicationEvent.event_key` evita duplicidade em reprocessamentos. Quando um
evento precisa ser recalculado, `radar rebuild-application-stage` recompoe o
estado resumido a partir da timeline persistida.

## Agenda Local

```mermaid
stateDiagram-v2
    [*] --> SUGGESTED
    [*] --> CONFIRMED: evento manual
    SUGGESTED --> CONFIRMED
    SUGGESTED --> DISMISSED
    SUGGESTED --> CANCELLED
    CONFIRMED --> COMPLETED
    CONFIRMED --> CANCELLED
    DISMISSED --> [*]
    COMPLETED --> [*]
    CANCELLED --> [*]
```

Eventos de agenda nao fazem integracao externa. Eventos manuais podem nascer
confirmados. Eventos vindos de descricao de vaga, e-mail importado ou estimativa
nascem como sugestao. Eventos estimados nunca viram compromisso confirmado sem
nova entrada manual mais confiavel.

Reaplicar a mesma transicao terminal ou de confirmacao e idempotente: o Radar
nao duplica auditoria nem reescreve timestamps. Edicoes estruturais de eventos
terminais sao bloqueadas para preservar historico.
