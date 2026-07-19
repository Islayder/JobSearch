# Fluxo de Revisao

O Radar de Vagas separa coleta, avaliacao automatica e decisao humana. A fila de
revisao mostra vagas locais que ainda podem ser avaliadas manualmente, sem abrir
links externos e sem enviar candidatura.

## Fila

```powershell
radar review-queue --limit 50
radar review-queue --status recommended
radar review-queue --review-state shortlisted
radar review-queue --provider gupy --min-score 70
radar review-queue --query-key gupy-estagio-dados
```

Por padrao a fila inclui `RECOMMENDED`, `ELIGIBLE`, `PENDING_REVIEW`, `SEEN` e
`NEW`. Vagas `APPLIED`, `DISMISSED` e `CLOSED` ficam fora da fila padrao.

## Acoes Manuais

```powershell
radar mark-seen 123
radar shortlist 123
radar dismiss-job 123 --reason manual --notes "fora de foco"
radar restore-job 123
```

Cada acao grava um `JobReviewEvent` append-only. O estado atual fica em
`JobReviewState`, uma linha por vaga.

As transicoes aceitas sao controladas por uma politica unica:

- `UNREVIEWED` pode virar `SEEN`, `SHORTLISTED`, `DISMISSED` ou `APPLIED`.
- `SEEN` pode virar `SHORTLISTED`, `DISMISSED` ou `APPLIED`.
- `SHORTLISTED` pode voltar para `SEEN` ou virar `DISMISSED` ou `APPLIED`.
- `DISMISSED` volta para `UNREVIEWED` somente por `restore-job`.

A interface web expõe botoes somente quando a transicao e valida para o estado
atual. Remover dos favoritos usa a transicao `SHORTLISTED -> SEEN`, nao uma
alteracao direta de enum. Abas de vagas cobrem novas, recomendadas, favoritas,
aplicadas, aguardando revisao, descartadas e encerradas. A lista padrao esconde
aplicadas, descartadas, fechadas e expiradas ate que a aba/filtro correspondente
seja escolhido.

Repetir uma acao que nao muda o estado efetivo nao cria evento duplicado.
Transicoes contraditorias falham com erro claro antes de gravar alteracoes.

## Restauracao

`restore-job` remove o descarte manual da fila e reavalia a vaga com as regras
atuais. Tambem limpa motivo e notas de descarte ativos. Vagas `APPLIED`,
`CLOSED` e `EXPIRED` nao sao restauradas por esse comando, porque representam
historico protegido.

Vagas com candidatura existente ficam bloqueadas para `mark-seen`, `shortlist`
e `dismiss-job`. A continuidade deve ser registrada em `applications`,
`application-event` ou na agenda local.

## Guardas

A fila mostra a decisao do `ApplicationGuard` para evitar preparacao duplicada.
As decisoes possiveis sao:

- `ALLOW_PREPARATION`
- `TRACK_ONLY`
- `BLOCK_ALREADY_APPLIED`
- `MANUAL_REVIEW`
- `BLOCK_DISMISSED`
- `BLOCK_CLOSED`

Nenhuma dessas decisoes envia candidatura. Elas apenas orientam o uso manual do
sistema.
