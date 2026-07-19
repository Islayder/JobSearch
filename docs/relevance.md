# Relevancia Profissional

A relevancia profissional e uma classificacao deterministica baseada em regras
locais de `config/relevance_rules.yaml`. Nao usa IA, embeddings ou servicos
externos.

## Entrada Canonica

Dry-run, importacao persistida, coletas e `radar reevaluate-jobs` usam o mesmo
builder canonico de entrada. Essa entrada combina titulo, empresa, descricao,
departamento, area, requisitos, responsabilidades e tecnologias, com limites de
tamanho para manter a avaliacao reproduzivel.

Coletores devem preencher campos estruturados quando a plataforma fornece esses
dados, sem inventar conteudo. Quando campos estruturados estiverem ausentes, a
avaliacao continua usando os textos disponiveis.

## Status

- `CORE`: sinais principais de dados, BI, tecnologia, software, automacao, QA
  ou produto tech.
- `ADJACENT`: sinais fortes de credito, risco, fraude, pricing, planejamento,
  CRM, performance, People Analytics ou outros dominios proximos quando ha
  contexto suficiente de dados, analise, tecnologia, indicadores ou relatorios.
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

## Calibracao Adjacent

Termos adjacentes sao separados em fortes e contextuais. Um termo forte pode
sustentar `ADJACENT` sozinho quando nao ha bloqueio negativo. Um termo
contextual precisa aparecer com evidencias de apoio, como dados, analytics,
BI, indicadores, automacao ou tecnologia. Sinais fracos sem contexto ficam em
`MANUAL_REVIEW` ou `UNRELATED`, reduzindo falso positivo de vagas genericas.
