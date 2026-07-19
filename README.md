# Radar de Vagas

Radar de Vagas é uma aplicação local em Python para encontrar, organizar,
deduplicar, avaliar e acompanhar oportunidades profissionais compatíveis com um
único usuário. A versão atual trabalha com fixtures e arquivos locais JSON/CSV.

Não há coleta web, integração com Gmail, IA, geração de currículo, preenchimento
de formulários ou candidatura automática nesta etapa.

## Requisitos

- Python 3.12 ou superior
- Windows, PowerShell ou outro terminal compatível
- SQLite local

## Instalação

Se `uv` estiver disponível, você pode usá-lo para criar o ambiente. Caso
contrário:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Depois de ativar o ambiente, o comando `radar` fica disponível:

```powershell
.\.venv\Scripts\Activate.ps1
radar --help
```

Também é possível executar sem depender do script de console:

```powershell
.\.venv\Scripts\python.exe -m radar_vagas --help
```

## Fluxo Local

Inicialize o banco:

```powershell
radar init-db
```

Importe a fixture:

```powershell
radar import-fixture data/fixtures/jobs.json
```

Valide ou importe arquivos locais genéricos:

```powershell
radar validate-file data/fixtures/import-example.json
radar validate-file data/fixtures/import-example.csv
radar import-file data/fixtures/import-example.json --dry-run
radar import-file data/fixtures/import-example.csv --dry-run
radar import-file data/fixtures/import-example.json
radar import-file data/fixtures/import-example.csv --delimiter ";"
radar import-file data/fixtures/import-example.csv --report data/exports/import-report.json
```

Avalie as vagas:

```powershell
radar evaluate-all
```

Liste os resultados:

```powershell
radar list-jobs
radar list-jobs --status eligible
radar list-jobs --employment-type internship
radar list-jobs --work-model remote
radar list-jobs --city "Belo Horizonte"
radar list-jobs --min-score 60
```

Consulte uma vaga:

```powershell
radar show-job 1
```

Veja o resumo:

```powershell
radar stats
```

Verifique ambiente e configuração:

```powershell
radar show-config
radar doctor
```

## Testes e Qualidade

```powershell
pytest
ruff check .
ruff format --check .
mypy src
```

Os testes usam bancos temporários e não acessam a internet.

## Configuração

Arquivos de exemplo e regras ficam em `config/`:

- `profile.example.yaml`
- `profile.yaml`
- `eligibility_rules.yaml`
- `ranking_weights.yaml`
- `blocked_companies.yaml`
- `blocked_companies.example.yaml`
- `sources.example.yaml`

O sistema carrega `config/profile.yaml` por padrão. Se ele não existir, usa o
exemplo apenas para demonstração e avisa o usuário. Um caminho alternativo pode
ser informado por `RADAR_PROFILE_PATH` ou por `radar show-config --profile`.

O banco padrão fica em `data/database/radar.sqlite3`. Para sobrescrever:

```powershell
$env:RADAR_DATABASE_URL = "sqlite:///C:/caminho/para/radar.sqlite3"
```

## Importação Local

`data/imports/` existe para arquivos reais locais e é ignorado pelo Git, exceto
por `.gitkeep`. Exemplos versionados ficam em `data/fixtures/`.

Consulte `docs/import-format.md` para schema, aliases, enums, CSV, JSON,
dry-run e relatório.

## Escopo Atual

A versão atual entrega ingestão por JSON/CSV local, deduplicação determinística,
avaliação de elegibilidade, ranking explicável, auditoria de importação e CLI.
Coleta real de fontes, Gmail, IA, geração de currículo e candidatura automática
ficam fora desta etapa.
