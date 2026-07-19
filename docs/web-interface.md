# Interface Web Local

`radar web` inicia uma interface local em portugues para operar o banco SQLite
do Radar pelo navegador. Ela e uma camada fina sobre os servicos existentes:
nao substitui a CLI, nao roda coleta automatica ao abrir e nao envia
candidaturas.

## Instalacao

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
radar web
```

Para desenvolvimento e testes:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
```

Se o extra nao estiver instalado, `radar web` mostra a instrucao de instalacao
e encerra sem quebrar os comandos de CLI.

## Execucao

```powershell
radar web
radar web --port 8001
radar web --no-open-browser
radar web --debug
```

O host padrao e `127.0.0.1`. A interface bloqueia `0.0.0.0` e hosts que nao
sejam loopback. Antes de iniciar, o comando aplica migracoes do banco local e
tenta impedir duas instancias para o mesmo banco e porta.

## Configuracao

As preferencias locais ficam em `config/ui.local.yaml`, ignorado pelo Git. O
arquivo de exemplo e `config/ui.example.yaml`.

Campos:

- `timezone`: timezone IANA usado em formularios locais.
- `page_size`: tamanho padrao de paginacao.
- `auto_open_browser`: abre o navegador ao iniciar.
- `default_job_sort`: ordenacao inicial da lista de vagas.
- `default_job_filters`: filtros iniciais da lista de vagas.
- `theme_preference`: `system`, `light` ou `dark`.

A tela de perfil permite ajustar configuracoes basicas sem editar YAML.

## Telas

- Dashboard: fila de revisao, compromissos, candidaturas aguardando retorno,
  vagas recentes, saude de fontes e atalhos.
- Onboarding: privacidade, timezone, importacao YAML/JSON/TXT, criacao manual,
  revisao do perfil, ativacao e comparacao inicial de vagas.
- Vagas: busca textual, filtros, abas, paginacao, detalhe, favoritar,
  descartar, restaurar, registrar candidatura manual e criar evento.
- Candidaturas: lista filtravel, detalhe, timeline e novos eventos.
- Agenda: proximos eventos, calendario mensal, itens sem data, sugestoes e
  transicoes de confirmar, dispensar, concluir e cancelar.
- Perfil: perfil ativo, versoes, importacao, criacao manual, ativacao e
  comparacao em lote.
- Fontes: saude de boards, consultas e execucoes recentes, com acao manual de
  coleta.

## Seguranca Local

Todas as acoes mutaveis exigem CSRF. GETs nao alteram dados nem calculam
compatibilidade. Paginas usam autoescape do Jinja, sem `safe` para conteudo de
usuario. Links externos abrem com `rel="noopener noreferrer"`.

Uploads sao limitados por tamanho, extensao e conteudo aceito. A web nao faz
fetch de URLs externas pelo servidor, nao faz login, nao usa cookies de
plataformas, nao executa Playwright e nao preenche formularios.

## Coleta Manual

A tela de fontes pode iniciar uma coleta manual pelo executor interno do plano
de busca. So uma coleta roda por processo. O status e mantido em memoria e cada
execucao persistida registra `SourceRun`.
