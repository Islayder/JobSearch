# AGENTS.md

## Visão Geral

Radar de Vagas é um monólito modular em Python, local-first, com SQLite,
SQLAlchemy, Alembic, Typer e regras determinísticas para ingestão, deduplicação,
elegibilidade, ranking e acompanhamento de vagas.

Leia os detalhes em:

- `docs/product-spec.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/state-machine.md`
- `docs/source-strategy.md`
- `docs/application-policy.md`

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Comandos Principais

```powershell
radar init-db
radar import-fixture data/fixtures/jobs.json
radar validate-file data/fixtures/import-example.json
radar import-file data/fixtures/import-example.csv --dry-run
radar evaluate-all
radar list-jobs
radar stats
radar doctor
```

## Qualidade

```powershell
pytest
ruff check .
ruff format --check .
mypy src
```

## Regras de Contribuição

- Use nomes de código, classes, tabelas, colunas e funções em inglês.
- Mantenha mensagens de usuário e documentação em português do Brasil.
- Atualize a documentação quando contratos, estados, entidades ou regras mudarem.
- Não grave secrets, tokens, cookies, currículos reais ou dados pessoais.
- Não versionar arquivos reais de `data/imports/`, bancos locais ou relatórios gerados.
- Não adicione scraping agressivo, bypass de CAPTCHA ou automação contra regras das plataformas.
- Não implemente candidatura automática sem política explícita e revisão humana.
- Toda regra de elegibilidade deve ter teste.
- Preserve compatibilidade com Windows e comandos PowerShell.
- Não adicione dependências sem necessidade real.
- Não altere regras de negócio silenciosamente.
