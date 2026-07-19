# Estratégia de Fontes

## Fonte de Descoberta e Fonte Original

Uma fonte de descoberta indica onde a vaga foi encontrada, como LinkedIn,
Indeed ou alerta de e-mail. A fonte original pode ser o ATS ou página de carreira
onde a candidatura realmente acontece, como Gupy, Greenhouse, Lever ou Ashby.

`Posting` preserva a publicação encontrada. `Job` guarda a oportunidade
canônica quando a associação é segura.

## Resolução Canônica

A normalização usa nome de empresa, título, cidade, modalidade, URL e hash de
conteúdo. Duplicatas exatas de publicação são ignoradas. Duplicatas prováveis
entre fontes não são unidas automaticamente e ficam disponíveis para revisão
futura.

## Coleta Incremental

Fontes futuras devem registrar `SourceRun` com início, fim, status e contadores.
Coletores devem ser incrementais, respeitar termos das plataformas e evitar
requisições agressivas.

## Alertas de E-mail

E-mails serão tratados futuramente como fonte de descoberta e como atualização
de candidatura. Esta versão só cria a estrutura de persistência.

## APIs, ATS e Importação Manual

APIs oficiais e integrações de ATS devem ser preferidas quando existirem. A
entrada funcional desta etapa é local: fixture de teste e importação genérica
por JSON/CSV. Cada importação de arquivo registra auditoria de origem, incluindo
hash, formato, linha ou índice original e versão do schema.

## Ganho Incremental por Fonte

Cada fonte deve medir itens encontrados, criados, ignorados e duplicados para
avaliar se aumenta o conjunto de vagas úteis ou apenas repete oportunidades já
conhecidas.
