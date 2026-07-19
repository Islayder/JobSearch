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

O timezone e validado com a base IANA disponivel via `zoneinfo`. As datas sao
persistidas em UTC e o timezone original e preservado em `CareerEvent.timezone`.
`America/Sao_Paulo` e apenas o padrao local configuravel.

URLs de reuniao devem ser passadas cruas ao servico de agenda. A validacao do
servico aceita apenas HTTP/HTTPS, rejeita credenciais, localhost, dominios
`.local` e IPs privados literais, e retorna erro claro em vez de transformar a
entrada invalida em vazio.

Transicoes validas de `confirmation_status`:

- `SUGGESTED` para `CONFIRMED`, `DISMISSED` ou `CANCELLED`.
- `CONFIRMED` para `COMPLETED` ou `CANCELLED`.
- `DISMISSED`, `COMPLETED` e `CANCELLED` sao terminais.

Repetir uma operacao de estado ja aplicada e idempotente: retorna o mesmo
evento, nao muda timestamps e nao grava auditoria duplicada. `completed_at` so
e preenchido em `COMPLETED`; `cancelled_at` so e preenchido em `CANCELLED`.

Repetir uma criacao com o mesmo `event_key` retorna o evento existente somente
quando a identidade canonica e equivalente: tipo, vaga, candidatura, fonte,
horario e titulo relevante. Reusar a mesma chave para outro compromisso gera
erro.

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

## Interface Web

A tela `/agenda` renderiza calendario mensal no servidor, com semanas completas
de segunda a domingo, navegacao de mes anterior/proximo, destaque do dia atual,
lista do periodo e secao para eventos sem data. Filtros aceitam tipo, origem,
status, vaga e candidatura. A criacao usa seletores de vaga e candidatura; IDs
continuam sendo persistidos internamente, mas nao sao o fluxo principal para o
usuario.
