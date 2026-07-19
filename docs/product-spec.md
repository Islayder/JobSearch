# Especificação do Produto

## Objetivo

Radar de Vagas organiza oportunidades profissionais locais para um único
usuário, começando por arquivos JSON de teste. A aplicação separa publicações
coletadas de vagas canônicas, evita duplicatas exatas, avalia compatibilidade e
gera um ranking explicável.

## Usuário

O perfil inicial considera uma pessoa cursando Engenharia de Software na PUC
Minas, baseada em Belo Horizonte, MG, Brasil, com prioridade para oportunidades
remotas no Brasil, híbridas em Belo Horizonte e presenciais em Belo Horizonte.

## Tipos de Oportunidade

- Estágio
- Trainee
- Júnior
- Bolsa de inovação
- Outros ou desconhecidos para revisão manual

## Prioridade Geográfica

1. Remoto explicitamente disponível para residentes no Brasil.
2. Híbrido exclusivamente em Belo Horizonte.
3. Presencial exclusivamente em Belo Horizonte.

Cidades da região metropolitana, como Contagem, Betim, Nova Lima, Ribeirão das
Neves e Sabará, não são tratadas como Belo Horizonte.

## Regras de Elegibilidade

Estágio é aceito quando remoto no Brasil, híbrido em Belo Horizonte ou
presencial em Belo Horizonte. Remoto sem país claro fica em revisão manual.

Trainee é aceito quando remoto no Brasil, híbrido em Belo Horizonte ou
presencial em Belo Horizonte com até 6 horas por dia. Trainee presencial em Belo
Horizonte sem jornada fica em revisão manual; acima de 6 horas é incompatível.

Júnior é aceito somente quando remoto no Brasil ou híbrido em Belo Horizonte.
Qualquer vaga júnior presencial é incompatível, inclusive em Belo Horizonte.

Bolsa de inovação fica em revisão manual. Outros vínculos e vínculos
desconhecidos também ficam em revisão manual, salvo quando uma regra obrigatória
determina rejeição.

## Histórico e Empresas Bloqueadas

Empresas bloqueadas sempre tornam a vaga incompatível, considerando nome
canônico e aliases normalizados. Vagas já descartadas, arquivadas ou com
candidatura registrada não voltam ao ranking; elas permanecem rastreáveis.

## Remuneração

Não há salário, bolsa ou remuneração mínima eliminatória. Salário ausente não
elimina uma vaga e também não gera pontos no ranking.

## Candidatura

A preparação de candidatura é humana nesta versão. A aplicação registra a
estrutura de candidatura e eventos, mas não envia candidaturas, não preenche
formulários e não altera currículos.
