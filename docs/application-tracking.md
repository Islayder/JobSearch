# Acompanhamento de Candidaturas

O acompanhamento de candidaturas e local e manual. O Radar registra que uma
candidatura foi feita fora do sistema, acompanha eventos do processo e protege o
ranking contra recomendacoes duplicadas.

## Registrar Candidatura

```powershell
radar mark-applied 123 --platform gupy --external-reference APP-123
radar mark-applied 123 --applied-at 2026-07-19T12:00:00-03:00
```

O comando cria ou reutiliza uma `Application`, muda a vaga para `APPLIED`, grava
`JobReviewState=APPLIED`, define `stage=APPLIED` e adiciona um evento
`SUBMITTED`.

## Listar e Detalhar

```powershell
radar applications
radar applications --status interview
radar applications --platform gupy --company "Acme"
radar show-application 1
```

`show-application` mostra os eventos locais e a etapa atual do processo.

## Eventos

```powershell
radar application-event 1 --type INTERVIEW_INVITED --notes "convite recebido"
radar application-event 1 --type REJECTED --occurred-at 2026-07-18T10:00:00-03:00
```

Eventos sao append-only. Alguns eventos atualizam o status resumido e a etapa
da candidatura, por exemplo:

- `CONFIRMATION_RECEIVED` move a etapa para `AWAITING_UPDATE`
- `ASSESSMENT_INVITED` move para `ASSESSMENT_RECEIVED`
- `ASSESSMENT_COMPLETED` move para `ASSESSMENT_COMPLETED`
- `CASE_RECEIVED` move para `CASE_RECEIVED`
- `CASE_SUBMITTED` move para `CASE_SUBMITTED`
- `INTERVIEW_INVITED` move para `INTERVIEW_SCHEDULED`
- `INTERVIEW_COMPLETED` move para `INTERVIEW_COMPLETED`
- `REJECTED` move para `REJECTED`
- `OFFER_RECEIVED` move para `OFFER_RECEIVED`
- `WITHDRAWN` move para `WITHDRAWN`

## Politica

O sistema nao envia formularios, nao abre sessoes autenticadas de ATS, nao usa
Playwright e nao tenta candidatar automaticamente. O usuario continua fazendo a
candidatura no site externo e usa o Radar apenas para registro e acompanhamento.
