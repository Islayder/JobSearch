# Coletores

## Contrato

Coletores implementam um contrato pequeno:

- `Collector`
- `CollectionContext`
- `CollectionResult`
- `CollectedPosting`
- `CollectorError`

Coletores nao criam sessoes SQLAlchemy, nao acessam a CLI e nao persistem dados.
Eles retornam objetos compativeis com `ImportedPosting`, que seguem pelo mesmo
pipeline de normalizacao, deduplicacao, elegibilidade e ranking usado por
arquivos JSON/CSV.

## Pipeline

1. A CLI carrega `network.yaml` e `company_boards.yaml`.
2. O cliente HTTP central valida URL, DNS, redirects, metodo, tipo de conteudo e
   tamanho de resposta.
3. O coletor converte a resposta publica em `ImportedPosting`.
4. O orquestrador cria `SourceRun`, detecta duplicatas, atualiza itens
   conhecidos, cria revisoes e aplica fechamento incremental quando permitido.
5. O relatorio de coleta resume rede, encontrados, novos, conhecidos, alterados,
   duplicados, invalidos, elegibilidade e fechamentos.

## Snapshots Completos e Parciais

`complete_snapshot=true` significa que o coletor processou todo o conjunto que o
endpoint retornou e que o resultado pode ser usado para detectar ausencias.

`partial=true` significa que a coleta nao representa o board inteiro. Isso
acontece quando `--max-items` ou `default_max_items` corta o payload bruto, ou
quando itens invalidos precisaram ser ignorados. Nesses casos o relatorio traz
`raw_items`, `considered_items`, `processed_items`, `invalid_items` e
`truncated`, e o orquestrador nao incrementa ausencias nem fecha publicacoes.

HTTP 304 tambem nao incrementa ausencias, porque nada foi reprocessado.

## JobPosting

`radar import-url <url>` coleta uma unica pagina publica, sem seguir links
encontrados no HTML. O parser procura `script type="application/ld+json"`,
interpreta objetos diretos, listas e `@graph`, e aceita `@type` como string ou
lista contendo `JobPosting`.

Com multiplos objetos:

```powershell
radar import-url "https://empresa.example/vagas" --all --dry-run
radar import-url "https://empresa.example/vagas" --select 2 --dry-run
```

Sem `--all` ou `--select`, multiplos objetos geram erro claro.

Campos mapeados incluem titulo, descricao HTML saneada, empresa, identificador,
URL, datas, tipo, localidade, remoto, escopo territorial, salario, beneficios e
metadados relevantes.

## Greenhouse

`greenhouse` usa somente o endpoint publico de board:

```powershell
radar collect-board greenhouse --board-token empresa --company "Empresa" --dry-run
```

Campos mapeados: id, titulo, localidade, conteudo HTML saneado, URL publica,
departamentos, escritorios, metadata e data de atualizacao quando presente.
Itens sem titulo, identificador ou URL publica valida sao ignorados como erro
recuperavel do item, sem criar titulo artificial.

## Lever

`lever` usa a Postings API publica:

```powershell
radar collect-board lever --board-token empresa --company "Empresa" --dry-run
```

Campos mapeados: id, titulo, categories, commitment, equipe, departamento,
localidade, modalidade, descricao combinada, hosted URL, apply URL e data de
criacao. Itens sem titulo, identificador ou URL publica valida sao ignorados
como erro recuperavel do item, sem criar titulo artificial.

## Adicionar Coletor Futuro

1. Adicione o modulo em `src/radar_vagas/collectors/<slug>/`.
2. Use apenas o cliente HTTP central.
3. Retorne `CollectionResult` com `ImportedPosting`.
4. Registre o coletor em `collectors/registry.py`.
5. Adicione fixtures offline e testes de mapping, erro, dry-run e incremental.
6. Documente limites e campos mapeados.

## Limitacoes

Nao ha descoberta automatica de boards, crawling recursivo, busca no Google,
Playwright, login, CAPTCHA, proxy, rotacao de user-agent, candidatura ou POST.
