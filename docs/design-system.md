# Design System da Interface Local

Este documento descreve o redesign visual da interface local do Radar de Vagas.
O objetivo e manter uma experiencia de produtividade clara, consistente e
acessivel, sem introduzir CDN, framework CSS, fonte externa, bundler ou pacote
npm.

## Identidade

A referencia principal e tema claro, com fundo cinza-azulado, superficies
brancas e azul para acoes principais.

Tokens centrais:

- `--color-primary-50` a `--color-primary-900`: escala azul.
- `--color-background`: fundo geral.
- `--color-surface`: superficies principais.
- `--color-surface-soft`: fundos de apoio.
- `--color-text` e `--color-text-muted`: texto principal e secundario.
- `--color-border` e `--color-border-dark`: bordas discretas.
- `--color-success`, `--color-warning`, `--color-danger`, `--color-info` e
  `--color-purple`: estados sem depender apenas de cor.
- `--shadow-xs`, `--shadow-sm`, `--shadow-card` e `--shadow-floating`: sombras
  leves.
- `--radius-sm`, `--radius-md`, `--radius-lg` e `--radius-xl`: raios de borda.

O tema escuro usa fundo azul-grafite, superficies diferenciadas, bordas
discretas e azul mais claro para acoes. A preferencia do servidor continua
vindo de `config/ui.local.yaml`; o controle no header pode sobrescrever apenas
a sessao local do navegador via `localStorage`, sem dados pessoais.

## Organizacao CSS

`src/radar_vagas/web/static/app.css` e o arquivo de entrada e importa apenas
arquivos locais:

- `css/tokens.css`
- `css/reset.css`
- `css/layout.css`
- `css/components.css`
- `css/forms.css`
- `css/pages.css`
- `css/resume-import.css`
- `css/responsive.css`

Nao ha dependencia externa, Google Fonts, Bootstrap, Tailwind, Material UI ou
build step.

## Componentes Jinja

Os componentes ficam em `src/radar_vagas/web/templates/components/`.

- `icons.html`: SVGs inline locais com `aria-hidden` quando decorativos.
- `navigation.html`: sidebar, header, busca rapida e controle de tema.
- `ui.html`: botoes, badges, metricas, alertas, breadcrumbs, estados vazios,
  paginacao, timeline e skeleton simples.

Componentes devem ser importados `with context` quando acessarem
`page_chrome`, `request`, `ui` ou `csrf_token`.

## App Shell

Todas as paginas usam skip link, `aside`, `header`, `main`, `footer` e
`aria-current="page"` no item ativo. A sidebar contem navegacao principal,
navegacao secundaria, indicador de dados locais e texto curto de privacidade.
No desktop ela fica fixa. Em telas medias fica compacta. Em telas pequenas vira
drawer com botao, `aria-expanded`, Escape e clique fora.

## Navegacao

`PageChrome` em `radar_vagas.web.routes.common` centraliza titulo contextual,
descricao, breadcrumb, item ativo e acao primaria. Essa camada nao faz consultas
ao banco.

O header inclui busca rapida GET para `/jobs` com campo `q`, funcionando sem
JavaScript.

## Formularios e Botoes

Campos devem ter label vinculado e ajuda visual proxima quando necessaria.

Variantes:

- primary: salvar, confirmar, criar, executar e avancar.
- secondary: voltar, cancelar, fechar e limpar.
- success: concluir ou confirmar item.
- danger: descartar, excluir, rejeitar, desistir e limpar rascunho.
- ghost: acoes discretas.

Nao use vermelho para acoes comuns.

## Badges e Estados

Badges usam texto sempre visivel. Cor e apenas reforco: verde para sucesso,
laranja para atencao, vermelho para perigo, azul/ciano para informacao e roxo
para testes ou estados auxiliares.

Estados vazios devem ter titulo, explicacao e acao util quando aplicavel.

## Responsividade

Validar pelo menos 1440 x 900, 1280 x 720, 1024 x 768, 768 x 1024 e 390 x 844.
Desktop usa duas colunas quando util. Tablet compacta a sidebar. Mobile usa
drawer, cards empilhados, header compacto, tabelas dentro de `.table-wrap` e
sem scroll horizontal global.

## Acessibilidade

Regras principais: `lang="pt-BR"`, foco visivel, landmarks semanticos,
headings em ordem, labels em campos, `aria-current`, `aria-expanded`,
`aria-controls`, icones decorativos com `aria-hidden`, alertas com
`role="status"` ou `role="alert"`, suporte a `prefers-reduced-motion` e nenhuma
informacao dependente apenas de cor.

## JavaScript

`app.js` e progressivo e local. Ele cobre drawer mobile, fechamento por Escape e
clique fora, alternancia visual de tema, fechamento de alertas, confirmacao de
formularios perigosos, repeticao de linhas no perfil manual, dropzone do
importador e polling existente da coleta.

A aplicacao segue utilizavel sem JavaScript para navegacao, formularios e acoes
principais.

## Assets Locais

O pacote inclui CSS modular, JS local e `static/icons/radar-mark.svg`. SVGs de
interface sao inline em componentes Jinja. Nao ha CDN nem asset remoto
obrigatorio.
