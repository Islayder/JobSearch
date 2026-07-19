# Especificacao do Produto

## Objetivo

Radar de Vagas organiza oportunidades profissionais locais para um unico
usuario. A aplicacao separa publicacoes coletadas de vagas canonicas, evita
duplicatas exatas, avalia compatibilidade e gera um ranking explicavel.

## Entradas

- fixtures e arquivos locais JSON/CSV;
- paginas publicas com JSON-LD `JobPosting`;
- boards publicos Greenhouse;
- boards publicos Lever.
- consultas publicas Gupy em modo `public_portal`.
- historico local de candidaturas em JSON/CSV;
- perfil profissional/curriculo local em YAML, JSON ou TXT estruturado;
- agenda local registrada manualmente ou derivada de dados locais.

Nao ha crawling recursivo, IA, Gmail, geracao automatica de curriculo,
Google Calendar, preenchimento de formularios ou candidatura automatica.

## Usuario

O perfil inicial considera uma pessoa cursando Engenharia de Software na PUC
Minas, baseada em Belo Horizonte, MG, Brasil, com prioridade para oportunidades
remotas no Brasil, hibridas em Belo Horizonte e presenciais em Belo Horizonte.

## Tipos de Oportunidade

- Estagio
- Trainee
- Junior
- Bolsa de inovacao
- Outros ou desconhecidos para revisao manual

## Prioridade Geografica

1. Remoto explicitamente disponivel para residentes no Brasil.
2. Hibrido exclusivamente em Belo Horizonte.
3. Presencial exclusivamente em Belo Horizonte.

Cidades da regiao metropolitana, como Contagem, Betim, Nova Lima, Ribeirao das
Neves e Sabara, nao sao tratadas como Belo Horizonte.

## Regras de Elegibilidade

Estagio e aceito quando remoto no Brasil, hibrido em Belo Horizonte ou
presencial em Belo Horizonte. Remoto sem pais claro fica em revisao manual.

Trainee e aceito quando remoto no Brasil, hibrido em Belo Horizonte ou
presencial em Belo Horizonte com ate 6 horas por dia. Trainee presencial em Belo
Horizonte sem jornada fica em revisao manual; acima de 6 horas e incompativel.

Junior e aceito somente quando remoto no Brasil ou hibrido em Belo Horizonte.
Qualquer vaga junior presencial e incompativel, inclusive em Belo Horizonte.

Bolsa de inovacao fica em revisao manual. Outros vinculos e vinculos
desconhecidos tambem ficam em revisao manual, salvo quando uma regra obrigatoria
determina rejeicao.

## Relevancia Profissional

Apos regras duras de empresa, candidatura, localidade e tipo, o sistema avalia
area profissional por regras deterministicas. `CORE` segue elegibilidade normal
e recebe bonus de ranking. `ADJACENT` pode ser elegivel com bonus menor.
`MANUAL_REVIEW` envia a vaga para revisao quando nao houver outro motivo
eliminatorio. `UNRELATED` torna a vaga incompativel com motivo explicito.

## Historico e Empresas Bloqueadas

Empresas bloqueadas sempre tornam a vaga incompativel, considerando nome
canonico e aliases normalizados. Vagas ja descartadas, arquivadas ou com
candidatura registrada nao voltam ao ranking; elas permanecem rastreaveis.

## Remuneracao

Nao ha salario, bolsa ou remuneracao minima eliminatoria. Salario ausente nao
elimina uma vaga e tambem nao gera pontos no ranking.

## Candidatura

A preparacao de candidatura e humana nesta versao. A aplicacao registra a
estrutura de candidatura, fila de revisao e eventos, mas nao envia
candidaturas, nao preenche formularios e nao altera curriculos.

Estados de revisao e candidatura passam por uma politica central. Vagas
descartadas, aplicadas, fechadas ou com candidatura existente nao sao
recolocadas no fluxo manual comum por uma acao contraditoria.

## Perfil Profissional

O perfil profissional e importado localmente e versionado. Ele pode conter
habilidades com evidencias, experiencias, projetos, formacao e idiomas. A
comparacao vaga-curriculo preserva a versao usada na analise e diferencia
`MATCHED`, `PARTIAL`, `NOT_PROVEN`, `NOT_MATCHED` e `AMBIGUOUS`, separando
requisitos obrigatorios de desejaveis.

Comparacoes sao historicas. A mesma vaga com a mesma versao de perfil, mesma
versao de regras e mesmo hash de conteudo retorna o resultado existente; quando
algum desses elementos muda, uma nova comparacao e criada.

## Agenda Local

A agenda local registra prazos, entrevistas, testes, cases, documentos,
respostas de oferta e follow-ups. Eventos podem estar ligados a vaga,
candidatura, ambas ou nenhuma delas. O sistema valida datas com timezone,
intervalos e URLs de reuniao, mas nao cria evento em calendario externo, nao
le e-mail e nao envia notificacao.
