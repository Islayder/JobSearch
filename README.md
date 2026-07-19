# Radar de Vagas

Radar de Vagas e uma aplicacao local em Python para encontrar, organizar,
deduplicar, avaliar e acompanhar oportunidades profissionais compativeis com um
unico usuario. A versao atual trabalha com fixtures, arquivos locais JSON/CSV e
coletores publicos de vagas.

Nao ha integracao com Gmail, IA, geracao de curriculo, preenchimento de
formularios ou candidatura automatica nesta etapa.

## Requisitos

- Python 3.12 ou superior
- Windows, PowerShell ou outro terminal compativel
- SQLite local

## Instalacao

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Depois de ativar o ambiente, o comando `radar` fica disponivel:

```powershell
.\.venv\Scripts\Activate.ps1
radar --help
```

Tambem e possivel executar sem depender do script de console:

```powershell
.\.venv\Scripts\python.exe -m radar_vagas --help
```

## Fluxo Local

Inicialize o banco:

```powershell
radar init-db
```

Importe fixtures e arquivos locais:

```powershell
radar import-fixture data/fixtures/jobs.json
radar validate-file data/fixtures/import-example.json
radar import-file data/fixtures/import-example.csv --dry-run
radar import-file data/fixtures/import-example.csv --report data/exports/import-report.json
```

Colete paginas e boards publicos:

```powershell
radar import-url "https://empresa.example/vaga/123" --dry-run
radar import-url "https://empresa.example/vaga/123" --all --dry-run
radar collect-board greenhouse --board-token empresa --company "Empresa" --dry-run
radar collect-board lever --board-token empresa --company "Empresa" --dry-run
radar collect-board empresa-configurada
radar collect-all --dry-run
radar collectors
radar boards
radar show-board empresa-configurada
radar source-health
```

Coletas Greenhouse e Lever com `--max-items` menor que o payload real sao
relatadas como snapshots parciais. Snapshots parciais, itens invalidos, falhas e
HTTP 304 nao fecham vagas ausentes.

Execute consultas publicas de descoberta:

```powershell
radar queries
radar show-query gupy-estagio-dados
radar collect-query gupy-estagio-dados --dry-run --max-pages 1 --max-items 5
radar collect-search-plan --collector gupy --tag data --dry-run --max-queries 2
radar query-health
```

Consultas de descoberta sao observacionais. Elas podem encontrar, atualizar e
registrar ocorrencias, mas nunca fecham publicacoes por ausencia.
O plano de busca aplica orcamento global de requisicoes, itens e duracao para
evitar expansao acidental de rede.

Avalie e consulte resultados:

```powershell
radar evaluate-all
radar reevaluate-jobs --dry-run
radar list-jobs
radar list-jobs --provider gupy --limit 25
radar show-job 1
radar stats
radar show-config
radar doctor
```

## Marco 4.1

O pipeline de descoberta Gupy usa a mesma entrada canonica de relevancia no
dry-run e na persistencia, preservando departamento, area, requisitos,
responsabilidades e tecnologias quando os coletores fornecem esses dados.
Consultas de descoberta continuam sem autoridade de fechamento: elas registram
observacoes e hits, mas nao transferem propriedade de escopo nem reabrem ou
fecham publicacoes de boards autoritativos.

`radar reevaluate-jobs` permite recalcular relevancia, elegibilidade e ranking
das vagas ja persistidas. A reavaliacao preserva estados protegidos como
`APPLIED`, `DISMISSED` e vagas com candidatura registrada.

## Testes e Qualidade

```powershell
pytest
ruff check .
ruff format --check .
mypy src
```

Os testes usam bancos temporarios, `httpx.MockTransport`, fixtures locais e uma
protecao que falha caso algum teste tente acessar internet real.

## Configuracao

Arquivos de exemplo e regras ficam em `config/`:

- `profile.example.yaml`
- `profile.yaml`
- `eligibility_rules.yaml`
- `ranking_weights.yaml`
- `blocked_companies.yaml`
- `blocked_companies.example.yaml`
- `network.yaml`
- `network.example.yaml`
- `company_boards.yaml`
- `company_boards.example.yaml`
- `search_queries.yaml`
- `search_queries.example.yaml`
- `relevance_rules.yaml`
- `sources.example.yaml`

O sistema carrega `config/profile.yaml` por padrao. Se ele nao existir, usa o
exemplo apenas para demonstracao e avisa o usuario. Um caminho alternativo pode
ser informado por `RADAR_PROFILE_PATH` ou por `radar show-config --profile`.

O banco padrao fica em `data/database/radar.sqlite3`. Para sobrescrever:

```powershell
$env:RADAR_DATABASE_URL = "sqlite:///C:/caminho/para/radar.sqlite3"
```

`config/company_boards.local.yaml` pode conter boards reais locais e e ignorado
pelo Git.
`config/search_queries.local.yaml` pode conter ajustes locais de consultas e
tambem e ignorado pelo Git.

## Documentacao

- `docs/import-format.md`
- `docs/collectors.md`
- `docs/network-policy.md`
- `docs/board-configuration.md`
- `docs/source-strategy.md`
- `docs/data-model.md`
- `docs/state-machine.md`
- `docs/search-queries.md`
- `docs/relevance.md`
- `docs/gupy-collector.md`
- `docs/collector-development-playbook.md`

## Escopo Atual

A versao atual entrega ingestao por JSON/CSV local, coleta publica por JSON-LD
JobPosting, Greenhouse, Lever e Gupy Public Portal, deduplicacao deterministica,
avaliacao de elegibilidade, relevancia profissional, ranking explicavel,
auditoria de importacao/coleta e CLI.
Boards persistidos sao isolados por escopo estavel de coletor e key/token/URL;
o nome da empresa e apenas informacao auxiliar de exibicao.

Pandape, Solides, LinkedIn, Indeed, crawling recursivo, Gmail, IA, geracao de
curriculo, Playwright, formularios e candidatura automatica ficam fora desta
etapa.
