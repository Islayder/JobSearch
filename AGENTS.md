# AGENTS.md

## Visao Geral

Radar de Vagas e um monolito modular em Python, local-first, com SQLite,
SQLAlchemy, Alembic, Typer e regras deterministicas para ingestao, coleta,
deduplicacao, elegibilidade, ranking e acompanhamento de vagas.

Leia os detalhes em:

- `docs/product-spec.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/state-machine.md`
- `docs/source-strategy.md`
- `docs/application-policy.md`
- `docs/collectors.md`
- `docs/network-policy.md`
- `docs/board-configuration.md`

## Instalacao

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
radar import-url "https://empresa.example/vaga/123" --dry-run
radar collect-board greenhouse --board-token empresa --company "Empresa" --dry-run
radar collect-board lever --board-token empresa --company "Empresa" --dry-run
radar collect-board <board-key>
radar collect-all --dry-run
radar collectors
radar boards
radar source-health
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

## Regras de Contribuicao

- Use nomes de codigo, classes, tabelas, colunas e funcoes em ingles.
- Mantenha mensagens de usuario e documentacao em portugues do Brasil.
- Atualize a documentacao quando contratos, estados, entidades ou regras mudarem.
- Nao grave secrets, tokens privados, cookies, curriculos reais ou dados pessoais.
- Nao versionar arquivos reais de `data/imports/`, bancos locais ou relatorios gerados.
- Nao adicione scraping agressivo, bypass de CAPTCHA ou automacao contra regras das plataformas.
- Nao implemente candidatura automatica sem politica explicita e revisao humana.
- Coletores devem usar o cliente HTTP central em `radar_vagas.http`.
- Nao chame `httpx` diretamente dentro de coletores.
- `import-url` e redirects devem passar pela protecao SSRF antes de qualquer request.
- Testes de coletores devem usar fixtures locais e `httpx.MockTransport`.
- Rede real em testes e proibida.
- Coletores publicos so podem usar GET/HEAD.
- Nao implemente POST, candidatura, login, cookies de sessao, CAPTCHA, proxy, rotacao de user-agent ou Playwright.
- Toda coleta persistida deve registrar `SourceRun`.
- Ao alterar coleta incremental, teste idempotencia, revisoes, ausencia, fechamento, reabertura e falha sem fechamento.
- Toda regra de elegibilidade deve ter teste.
- Preserve compatibilidade com Windows e comandos PowerShell.
- Nao adicione dependencias sem necessidade real.
- Nao altere regras de negocio silenciosamente.
