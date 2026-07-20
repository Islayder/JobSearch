# Inteligencia de Empresas e Entrevistas

O Radar mantem informacoes locais de empresa para apoiar a revisao de vagas e a
preparacao de entrevista. A funcionalidade e manual, local-first e
deterministica.

## Origens

Toda afirmacao sobre uma empresa deve ficar em uma das categorias:

- `Informacao oficial`: site oficial, pagina publica da vaga ou comunicacao
  publica da empresa.
- `Relato de funcionarios`: snapshot informativo de relatos agregados, sempre
  exibido como percepcao e nunca como fato oficial.
- `Inferencia do Radar`: conclusao deterministica baseada nos dados locais da
  vaga, perfil ou comparacao.
- `Anotacao do usuario`: observacao digitada localmente pelo usuario.

As categorias nao devem ser mescladas na interface, na preparacao de entrevista
ou em relatorios futuros.

## Dados Locais

`CompanyProfile` guarda nome, site oficial, setor, tamanho quando conhecido,
localizacao, descricao, fontes e data de captura. `CompanyFact` guarda fatos e
anotacoes por origem. `CompanyReviewSnapshot` guarda plataforma, avaliacao
opcional, quantidade opcional, pontos positivos, pontos negativos, periodo,
origem e indicacao explicita de relatos de funcionarios.

`InterviewPreparation` guarda uma preparacao gerada para uma vaga. Ela usa a
vaga, a empresa, a versao de perfil ativa quando existir, a comparacao atual
quando disponivel e as fontes registradas. Quando uma informacao nao existe, a
saida deve indicar `nao encontrado`.

## Limites

A funcionalidade nao faz scraping autenticado, login, CAPTCHA, bypass, sessao
externa, crawling agressivo ou chamadas HTTP proprias. Tambem nao cria
candidaturas, nao muda status e nao cria eventos de agenda automaticamente.

A preparacao nao deve inventar cultura, salarios, beneficios, tecnologias,
perguntas realmente feitas pela empresa ou etapas do processo seletivo.
