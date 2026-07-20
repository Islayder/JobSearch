# Setup Local

Este guia resume o ambiente local para desenvolvimento e validacao do Radar de
Vagas em Windows.

## Ambiente

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
.\.venv\Scripts\Activate.ps1
```

O extra `web` e opcional para uso de CLI, mas necessario para `radar web` e
para os testes da interface. Ele tambem instala os leitores locais de PDF
textual e DOCX usados pelo importador revisado de curriculo.

## Banco Local

```powershell
radar init-db
radar doctor
radar stats
```

O banco padrao fica em `data/database/radar.sqlite3` e e ignorado pelo Git. Para
testar com um banco temporario:

```powershell
$env:RADAR_DATABASE_URL = "sqlite:///C:/Temp/radar-teste.sqlite3"
radar init-db
```

## Configuracoes Locais

Arquivos `*.local.yaml` em `config/` sao locais e ignorados pelo Git. Exemplos:

- `config/company_boards.local.yaml`
- `config/search_queries.local.yaml`
- `config/profile.local.yaml`
- `config/professional_profile.local.yaml`
- `config/ui.local.yaml`

Use `config/ui.example.yaml` como referencia para preferencias da interface
web.

## Validacao

```powershell
pytest
ruff check .
ruff format --check .
mypy src
git diff --check
```

Os testes usam bancos temporarios e bloqueiam rede real. Testes de coletores
devem usar fixtures locais e `httpx.MockTransport`.

## Interface Web

```powershell
radar web
radar web --port 8001 --no-open-browser
```

A interface abre em `http://127.0.0.1:8000` por padrao. Ela e local-first,
aplica migracoes antes de iniciar e nao deve ser exposta em rede.

O visual da interface usa CSS modular e assets locais. Para validar o redesign
durante desenvolvimento, abra dashboard, vagas, detalhe de vaga, candidaturas,
agenda, perfil, importador de curriculo, fontes e uma pagina de erro em
larguras desktop, tablet e mobile. O drawer mobile deve abrir pelo botao do
header, fechar por Escape e clique fora, e manter foco visivel.

Para validar fluxos web manualmente em banco temporario, conclua o onboarding,
importe um curriculo PDF textual ou DOCX, revise itens, edite um candidato,
remova/restaure outro, confirme o perfil, descarte um rascunho separado, crie
um perfil manual, filtre vagas, favorite/desfavorite, descarte/restaure,
registre candidatura, atualize etapas, crie evento de agenda e execute uma
coleta mockada ou uma coleta real autorizada. A coleta web roda em segundo
plano e bloqueia um segundo disparo enquanto estiver ativa.
