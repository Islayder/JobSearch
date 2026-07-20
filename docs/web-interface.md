# Interface Web Local

`radar web` inicia uma interface local em portugues para operar o banco SQLite
do Radar pelo navegador. Ela e uma camada fina sobre os servicos existentes:
nao substitui a CLI, nao roda coleta automatica ao abrir e nao envia
candidaturas.

A interface usa um app shell com sidebar, header, busca rapida, breadcrumbs,
conteudo principal e footer. O redesign e apenas visual e de experiencia:
preserva rotas, CSRF, GETs sem mutacao, regras de dominio, compatibilidade,
coleta, agenda, importador de curriculo e acompanhamento manual.

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

- Dashboard: central diaria com saudacao local, resumo dinamico, metricas,
  fila de revisao, compromissos, candidaturas aguardando retorno, testes/cases,
  vagas recomendadas, saude de fontes e atalhos.
- Onboarding: privacidade, timezone, importacao YAML/JSON/TXT, criacao manual,
  importacao revisada de curriculo PDF/DOCX/TXT/Markdown, revisao do perfil,
  ativacao e comparacao inicial de vagas.
- Vagas: busca textual, busca rapida no header, abas, filtros agrupados,
  chips de filtros ativos, cartoes de vaga, paginacao, detalhe, favoritar,
  descartar, restaurar, registrar candidatura manual e criar evento.
- Candidaturas: lista filtravel por empresa, status, etapa, plataforma,
  periodo e atalhos de retorno/teste/case/entrevista/oferta/rejeicao/retirada;
  o detalhe mostra timeline vertical, agenda ligada, proxima acao e botoes de
  evento do fluxo.
- Agenda: calendario mensal real de segunda a domingo, navegacao por mes,
  botao de hoje, formulario de novo compromisso, filtros por
  tipo/origem/status/vaga/candidatura, mes em portugues sem depender do locale
  do sistema, lista limitada ao mes selecionado, itens sem data e transicoes de
  confirmar, dispensar, concluir e cancelar.
- Perfil: perfil ativo, habilidades, evidencias, experiencias, projetos,
  versoes, importacao revisada de curriculo, rascunhos, importacao estruturada,
  criacao manual, ativacao e comparacao em lote. Configuracoes locais ficam em
  area visual separada.
- Fontes: saude de boards, consultas e execucoes recentes, com acao manual de
  coleta em segundo plano. Itens ignorados geram aviso, mas nao sao apresentados
  automaticamente como execucao parcial.

## Seguranca Local

Todas as acoes mutaveis exigem CSRF. GETs nao alteram dados nem calculam
compatibilidade. Paginas usam autoescape do Jinja, sem `safe` para conteudo de
usuario. Links externos abrem com `rel="noopener noreferrer"`.

Uploads sao limitados por tamanho, extensao e conteudo aceito. A web nao faz
fetch de URLs externas pelo servidor, nao faz login, nao usa cookies de
plataformas, nao executa Playwright e nao preenche formularios.

Uploads de perfil nao sao persistidos como arquivos brutos. A interface le o
conteudo em blocos ate o limite permitido, valida extensao/UTF-8/conteudo e
envia os bytes ao servico de perfil. Apenas formato, hash e origem sanitizada
podem ficar registrados. O fluxo manual cria `ProfessionalProfileInput`
diretamente, sem JSON temporario em `data/imports`.

O fluxo revisado de curriculo aceita PDF textual, DOCX, TXT e Markdown com
limite de 8 MB. PDF protegido, PDF sem texto, `.doc` antigo, `.docm`, DOCX com
macros, referencias externas ou estrutura abusiva sao rejeitados com mensagem
humana. PDFs textuais sao avaliados por estrategia de extracao: automatico,
texto normal, layout e geometrico. Quando a qualidade fica degradada, a revisao
mostra aviso destacado e permite tentar outro modo por POST com CSRF e reenvio
do mesmo PDF. Nao ha OCR, servico externo, Word, LibreOffice ou Playwright.

Rascunhos de curriculo ficam em `/profile/resume/imports`. A tela de revisao
mostra candidatos por secao, indice lateral, resumo de pendencias, confianca,
explicacao, origem, trecho curto, modo de extracao, qualidade e estado. O
usuario pode salvar edicoes, confirmar, remover ou restaurar cada item. A
confirmacao final oferece "Ativar agora" e "Analisar vagas depois"; nenhuma
comparacao roda se a segunda opcao nao for marcada.

Habilidade digitada sem evidencia explicita e tratada como declarada, nao como
comprovada. Textarea e linhas estruturadas sao interpretados separadamente e
depois mesclados por nome normalizado; categoria, nivel e evidencia pertencem
somente a linha estruturada onde foram preenchidos. Experiencias e projetos
podem comprovar habilidades quando referenciam a skill; a interface nao inventa
evidencia.

Na lista de vagas, compatibilidade significa analise atual: mesma vaga, perfil
ativo, versao corrente das regras de perfil e hash atual do conteudo da vaga.
Filtros, ordenacao e score exibido ignoram analises historicas. No detalhe, uma
analise historica ainda fica acessivel, mas aparece como desatualizada quando o
perfil, as regras ou o conteudo da vaga mudaram.

## Design e Assets

O design system esta documentado em `docs/design-system.md`. Os estilos ficam
em `src/radar_vagas/web/static/css/`, importados por `app.css`. O JavaScript
fica em `app.js` e e progressivo. SVGs sao locais ou inline, sem bibliotecas de
icones externas, CDN, fonte externa, npm ou bundler.

## Coleta Manual

A tela de fontes pode iniciar uma coleta manual pelo executor interno do plano
de busca. So uma coleta roda por processo e o clique retorna imediatamente com
status consultavel pela propria pagina. O status e mantido em memoria e cada
execucao persistida registra `SourceRun`. Mensagens exibidas passam por
sanitizacao para remover query strings, tokens, caminhos locais, URLs completas
e detalhes de traceback.
