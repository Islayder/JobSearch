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
`SUBMITTED`. Quando a mesma identidade de candidatura ja existe, o comando
reusa o registro existente em vez de duplicar historico.

## Listar e Detalhar

```powershell
radar applications
radar applications --status interview
radar applications --platform gupy --company "Acme"
radar show-application 1
```

`show-application` mostra os eventos locais e a etapa atual do processo.

A interface web permite filtrar candidaturas por empresa, status, etapa,
plataforma, periodo e atalhos operacionais: aguardando retorno, teste/case,
entrevista, oferta, rejeitadas e retiradas. O detalhe mostra dados da vaga,
timeline, agenda ligada, notas e pagina oficial quando houver URL segura.

No filtro web por periodo, `from_date` e `to_date` sao interpretados no timezone
configurado da interface. `from_date` e o inicio local do dia; `to_date` inclui
o dia inteiro usando o inicio do dia seguinte como limite exclusivo em UTC.
Candidaturas sem `applied_at` nao aparecem quando ha filtro de periodo.

## Eventos

```powershell
radar application-event 1 --type INTERVIEW_INVITED --notes "convite recebido"
radar application-event 1 --type REJECTED --occurred-at 2026-07-18T10:00:00-03:00
radar rebuild-application-stage 1
```

Eventos sao append-only. Eventos importados podem usar `event_key`; repetir a
mesma chave para a mesma candidatura retorna o evento existente e nao cria nova
linha.

O status resumido e a etapa da candidatura sao derivados por um redutor de
timeline. O redutor processa todos os eventos em ordem cronologica e pode ser
executado novamente com `rebuild-application-stage` quando historicos antigos
forem corrigidos.

Alguns eventos atualizam o status resumido e a etapa da candidatura, por
exemplo:

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

Na web, os botoes principais seguem o fluxo: confirmacao recebida, teste
recebido/concluido, case recebido/enviado, entrevista marcada/concluida,
atualizacao, rejeicao, oferta e retirada. Rejeicao, oferta e retirada exigem
confirmacao explicita porque sao eventos terminais. A etapa visivel continua
sendo derivada pela timeline, nao editada manualmente.

Eventos informativos, como `CONFIRMATION_RECEIVED` e `PROCESS_UPDATE`, nao
regridem uma candidatura que ja chegou a etapa mais avancada. Um evento antigo
adicionado depois tambem nao derruba uma etapa mais recente. Eventos terminais,
como `REJECTED` ou `WITHDRAWN`, representam o estado atual somente se forem os
eventos efetivos mais recentes da timeline.

## Politica

O sistema nao envia formularios, nao abre sessoes autenticadas de ATS, nao usa
Playwright e nao tenta candidatar automaticamente. O usuario continua fazendo a
candidatura no site externo e usa o Radar apenas para registro e acompanhamento.
