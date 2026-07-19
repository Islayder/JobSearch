# Agenda Local

A agenda local registra prazos, entrevistas, testes, cases e follow-ups sem
integrar Google Calendar, Gmail ou notificacoes externas.

## Entidades

`CareerEvent` guarda o evento atual da agenda. Ele pode apontar para uma vaga,
uma candidatura, ambas ou nenhuma delas. Campos principais:

- `event_key`: identidade idempotente opcional para eventos importados ou
  sugeridos.
- `event_type`: `APPLICATION_DEADLINE`, `ASSESSMENT`,
  `ASSESSMENT_DEADLINE`, `CASE_DEADLINE`, `INTERVIEW`, `GROUP_DYNAMICS`,
  `DOCUMENT_DEADLINE`, `OFFER_RESPONSE_DEADLINE`, `FOLLOW_UP` ou `CUSTOM`.
- `source`: `MANUAL`, `JOB_DESCRIPTION`, `EMAIL` ou `ESTIMATED`.
- `confirmation_status`: `SUGGESTED`, `CONFIRMED`, `DISMISSED`,
  `COMPLETED` ou `CANCELLED`.
- `starts_at` e `ends_at`: armazenados em UTC quando presentes.
- `timezone`: timezone original informado.
- `meeting_url`: aceita somente URL HTTP/HTTPS segura.

`CareerEventAudit` registra criacao, atualizacao, confirmacao, conclusao e
cancelamento. A exclusao fisica nao e o fluxo padrao.

## Regras

Eventos manuais podem nascer como `CONFIRMED`. Eventos nao manuais nascem como
`SUGGESTED`. Eventos `ESTIMATED` nunca podem ser confirmados como compromisso
real.

Datas informadas devem conter timezone. `ends_at` nao pode ser anterior a
`starts_at`. Um evento ligado a uma candidatura deve pertencer a mesma vaga
quando `job_id` tambem for informado.

Repetir uma operacao com o mesmo `event_key` retorna o evento existente e nao
cria duplicata.

## CLI

```powershell
radar agenda
radar agenda --days 30
radar agenda --type interview
radar add-agenda-event --type interview --title "Entrevista" --starts-at 2026-07-21T10:00:00-03:00 --timezone America/Sao_Paulo
radar show-agenda-event 1
radar confirm-agenda-event 1
radar complete-agenda-event 1
radar cancel-agenda-event 1
```
