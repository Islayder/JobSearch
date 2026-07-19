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

## Restauracao

`restore-job` remove o descarte manual da fila e reavalia a vaga com as regras
atuais. Vagas `APPLIED` e `CLOSED` nao sao restauradas por esse comando, porque
representam historico protegido.

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
