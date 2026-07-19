# Importacao de Historico de Candidaturas

Historicos antigos podem ser importados por JSON ou CSV. O modo seguro e sempre
validar ou simular antes de persistir.

## Comandos

```powershell
radar validate-application-history data/imports/applications.csv
radar import-application-history data/imports/applications.csv --dry-run
radar import-application-history data/imports/applications.csv --report data/exports/applications.json
radar import-application-history data/imports/applications.csv --no-dry-run
```

Por padrao, `import-application-history` roda em dry-run. Para gravar, use
`--no-dry-run`.

## Campos

Os arquivos aceitam os mesmos campos em JSON ou CSV:

- `provider_identity_key`
- `application_url`
- `company`
- `title`
- `platform`
- `applied_at`
- `status`
- `external_reference`
- `notes`

Pelo menos um destes identificadores deve existir:

- `provider_identity_key`
- `application_url`
- `external_reference`
- `company` + `title`

`status` aceita somente estados que representam eventos reais de historico:

- `SUBMITTED`
- `TEST`
- `INTERVIEW`
- `REJECTED`
- `OFFER`
- `WITHDRAWN`

Estados operacionais internos, como `PREPARING`, `AWAITING_REVIEW`, `READY`,
`FINAL_STAGE` e `CLOSED`, sao rejeitados com erro de validacao porque nao
descrevem um evento externo comprovavel.

## Matching

O importador classifica cada linha como:

- `EXACT`: identidade de plataforma, URL ou referencia externa apontou para uma
  unica vaga.
- `PROBABLE`: empresa e titulo apontaram para uma unica vaga, mas sem identidade
  forte.
- `UNMATCHED`: nenhuma vaga local encontrada.
- `CONFLICT`: mais de uma vaga possivel.

Matches provaveis nao sao aplicados automaticamente, a menos que o comando seja
executado com `--allow-probable-matches`.

## Persistencia

Quando uma linha e ligada a uma vaga, o sistema cria ou atualiza `Application`,
marca a vaga como `APPLIED`, cria eventos derivados do status e grava
`ApplicationMatch` com evidencias resumidas. O arquivo de entrada deve ficar em
`data/imports/`, que e ignorado pelo Git.

Cada linha recebe um fingerprint deterministico derivado dos campos canonicos da
linha. `ApplicationEvent.event_key` e `ApplicationMatch.fingerprint` tornam a
importacao idempotente: reimportar o mesmo arquivo deve resultar em itens
inalterados, sem duplicar candidaturas, eventos ou matches.

Linhas `UNMATCHED`, `CONFLICT` ou `PROBABLE` sem permissao de aplicacao entram
como `needs_review`. Elas preservam a evidencia do match, mas nao alteram vaga
nem candidatura automaticamente.

Dry-run nao grava mudancas e nao executa rollback na sessao do chamador. Isso
permite validar o arquivo dentro de fluxos maiores sem desfazer alteracoes ja
controladas por outra transacao.

O relatorio da importacao separa criados, atualizados, inalterados, ignorados,
erros, itens que precisam de revisao e matches criados.
