# Politica de Rede

## Metodos Permitidos

Somente GET e HEAD sao permitidos. POST, PUT, PATCH e DELETE nao
fazem parte da infraestrutura de coleta.

## Cliente Central

Todo coletor deve usar `radar_vagas.http.client.HttpClient`. O cliente aplica:

- user-agent identificavel;
- timeouts configuraveis;
- redirects limitados;
- validacao de cada destino de redirect;
- limite de resposta;
- validacao de tipo de conteudo;
- retry conservador para GET idempotente;
- headers de cache.
- allowlist opcional de hosts por coletor, aplicada tambem a redirects.

## SSRF

`import-url` recebe URL arbitraria e passa por validacao antes da request.

Sao aceitos somente `http` e `https`. Sao rejeitados:

- `file://`, `ftp://`, `data:` e `javascript:`;
- URL sem host;
- credenciais embutidas;
- localhost;
- hosts `.local`;
- IPs loopback, privados, link-local, multicast, reservados ou nao globais;
- portas fora da politica, por padrao somente 80 e 443.

O DNS e resolvido antes da request e todos os IPs retornados sao validados.
Imediatamente antes de cada tentativa de envio, o destino e revalidado de novo;
isso bloqueia o caso em que a primeira resolucao parece publica e a segunda ja
retorna endereco privado. Redirects passam pela mesma politica.

Limite residual: o transporte padrao do `httpx` ainda faz a conexao usando o
hostname original, portanto a pilha de rede do sistema pode resolver o nome
novamente depois da segunda validacao feita pela aplicacao. O projeto nao
desativa TLS, nao usa `verify=False` e nao troca HTTPS por conexao manual a IP
fixo nesta etapa. Por isso a politica reduz a janela de DNS rebinding, mas nao
deve ser descrita como protecao criptograficamente completa contra rebinding em
ambientes DNS hostis.

## Timeouts e Retry

Padroes em `config/network.yaml`:

- connect: 10 segundos;
- read: 30 segundos;
- write: 10 segundos;
- pool: 10 segundos;
- max redirects: 5;
- max response bytes: 5 MB;
- max retries: 2;
- backoff: 0.5 segundo.

Retry automatico ocorre somente para GET em timeout, erro de conexao, 429, 502,
503 e 504. `Retry-After` e respeitado quando valido. Testes injetam espera falsa
e nao aguardam delays reais.

## Tipos de Conteudo

Permitidos:

- `application/json`;
- tipos `+json`;
- `application/ld+json`;
- `text/html`;
- `application/xhtml+xml`.

Tipos inesperados geram erro controlado e nao sao persistidos.

## Cache

Quando um board persistido recebe `ETag` ou `Last-Modified`, a proxima coleta
envia `If-None-Match` e `If-Modified-Since`. HTTP 304 e sucesso sem
reprocessamento, sem incremento de ausencia e sem fechamento.

## Gupy

O coletor Gupy usa apenas a interface publica validada do portal:

- host: `employability-portal.gupy.io`;
- caminho: `/api/v1/jobs`;
- parametros: `jobName`, `limit`, `offset`;
- metodos: GET e HEAD publicos.

Nao usa `https://api.gupy.io/api/v1/jobs`, Bearer token, login, cookie de
sessao, POST, candidatura, CAPTCHA, proxy ou Playwright. URLs publicas de vaga
em subdominios `*.gupy.io` podem ser preservadas, mas nao sao acessadas como
candidatura.

## Baixa Concorrencia

`network.yaml` define `max_parallel_requests` e intervalo minimo por board. A CLI
executa de forma conservadora e nao tenta contornar bloqueios.

## Proibicoes

Nao implementar crawling recursivo, login, cookies de sessao,
CAPTCHA, fingerprint de navegador, proxies, rotacao de identidade, Playwright,
envio de formulario ou candidatura.
