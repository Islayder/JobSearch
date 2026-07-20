# Politica de Privacidade Local

Radar de Vagas e local-first. Dados pessoais, banco SQLite, importacoes,
exportacoes e credenciais locais nao devem ser versionados.

## Arquivos Ignorados

O `.gitignore` cobre:

- `.env`
- `.venv/`
- caches de Python, pytest, Ruff e mypy;
- bancos SQLite em `data/database/`;
- backups em `data/database/backups/`;
- `data/imports/`;
- `data/exports/`;
- `data/personal/`;
- `data/resumes/`;
- `data/curricula/`;
- `data/backups/`;
- `config/profile.local.yaml`;
- `config/professional_profile.local.yaml`;
- `config/ui.local.yaml`;
- demais `config/*.local.yaml` sensiveis;
- credenciais e tokens locais.

Fixtures, exemplos e mensagens de teste devem usar dados sinteticos. Caminhos
de usuario real, e-mails pessoais, telefones, nomes reais de curriculo, cookies,
tokens, links reais de reuniao e bancos SQLite locais nao devem aparecer em
arquivos versionados. Quando for necessario demonstrar um caminho local, use um
valor ficticio como `C:\Users\ExampleUser\...`.

Curriculos e resumes reais continuam fora do Git por diretorio sensivel ou por
decisao operacional. Codigo, templates, documentacao, testes e fixtures
sinteticas nao devem ser ignorados apenas porque possuem `resume`, `curriculo`
ou `currículo` no nome.

Assets da interface web sao locais e versionaveis quando nao contem dados
pessoais: CSS, JavaScript, SVGs genericos e componentes Jinja. Screenshots de
validacao visual, bancos, relatorios, curriculos reais e arquivos importados
continuam fora do Git.

## Curriculos

Arquivos de curriculo pessoais devem ficar fora do Git. O sistema pode importar
um arquivo local estruturado, gravar hash e versao no banco local, mas nao
versiona o arquivo real, nao gera curriculo e nao envia documentos para
plataformas.

Pela web, PDF textual, DOCX, TXT e Markdown podem ser extraidos para revisao
humana. O conteudo bruto e descartado apos a extracao; o banco guarda apenas
hash, nome sanitizado, formato, contadores, avisos e candidatos isolados com
trechos curtos de origem. Linhas de contato sao ignoradas pelo parser e nao
devem ser usadas como habilidade, experiencia ou evidencia.

## Agenda

Eventos de agenda sao locais. O Radar nao cria eventos em Google Calendar, nao
le Gmail, nao envia notificacoes externas e nao abre sessoes autenticadas.

`meeting_url` e armazenada somente quando informada manualmente ou importada de
fonte local autorizada. A validacao aceita apenas HTTP/HTTPS e rejeita URLs com
credenciais, localhost, dominios `.local` e IPs privados literais. Essa
validacao nao faz consulta de rede.

Notas da agenda e das candidaturas podem conter contexto pessoal. Elas ficam no
banco SQLite local e nos relatorios locais ignorados pelo Git.

## Interface Web Local

A interface web roda por padrao em `127.0.0.1` e nao deve ser publicada em rede.
Ela usa cookies locais com `SameSite=Strict`, protecao CSRF em acoes mutaveis,
cabecalhos de seguranca e validacao de upload. GETs nao alteram dados.

Arquivos importados pela interface aceitam YAML, YML, JSON ou TXT estruturado
no fluxo legado e PDF textual, DOCX, TXT ou Markdown no fluxo revisado de
curriculo. A interface nao busca URLs externas pelo servidor, nao faz login,
nao envia curriculo, nao preenche formularios e nao faz OCR.

Uploads web de perfil nao sao gravados em `data/imports` nem preservados como
arquivo temporario de negocio. A leitura acontece em blocos com limite de
tamanho; em sucesso ou erro, o conteudo bruto e descartado. O banco pode manter
hash, formato e origem sanitizada, mas nao caminho temporario inutil nem
conteudo do arquivo. Imports feitos pela CLI continuam podendo registrar o
caminho escolhido pelo usuario.

O importador revisado rejeita PDF protegido ou sem texto, `.doc` antigo,
`.docm`, DOCX com macros, referencias externas, caminhos internos abusivos,
compressao abusiva ou tamanho acima dos limites locais. Mensagens de erro nao
devem expor caminhos locais, bytes, XML interno, nomes de bibliotecas ou
traceback.

Mensagens de coleta exibidas na web sao sanitizadas. Query strings, tokens,
cookies, caminhos locais, URLs completas, tracebacks e corpo de resposta nao
devem aparecer em tela ou logs de status da interface.

## Exportacoes

Relatorios em `data/exports/` podem conter dados operacionais ou pessoais. Eles
sao ignorados por padrao; compartilhe manualmente apenas quando necessario.
