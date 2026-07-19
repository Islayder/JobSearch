# Relevancia Profissional

A relevancia profissional e uma classificacao deterministica baseada em regras
locais de `config/relevance_rules.yaml`. Nao usa IA, embeddings ou servicos
externos.

## Status

- `CORE`: sinais principais de dados, BI, tecnologia, software, automacao, QA
  ou produto tech.
- `ADJACENT`: sinais de credito, risco, fraude, pricing, planejamento, CRM,
  performance, People Analytics, operacoes orientadas a dados, processos,
  indicadores ou relatorios.
- `MANUAL_REVIEW`: sinal fraco ou ambiguo que merece revisao humana.
- `UNRELATED`: sem sinais suficientes ou com sinais negativos fortes.

## Precedencia

Empresa bloqueada, candidatura existente, localidade incompativel e tipo
incompativel prevalecem. Depois disso:

- `CORE` segue elegibilidade e recebe bonus de ranking;
- `ADJACENT` segue elegibilidade e recebe bonus menor;
- `MANUAL_REVIEW` transforma uma vaga elegivel em revisao manual;
- `UNRELATED` transforma uma vaga sem outro bloqueio em incompativel.

`APPLIED`, `DISMISSED` e candidaturas existentes nao sao sobrescritos por
reavaliacao automatica.

## Explicacao

`Decision` guarda `relevance_status`, `relevance_score`,
`relevance_reason_json` e `relevance_rules_version`. O JSON inclui termos
encontrados por campo, pontuacoes e explicacao.
