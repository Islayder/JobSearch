# Perfil Profissional

O Marco 5A adiciona perfil profissional local, versionado e explicavel. O
arquivo real do usuario deve ficar fora do Git, por exemplo em
`data/personal/`, `data/resumes/` ou `config/professional_profile.local.yaml`.

## Importacao

O comando aceita YAML, JSON ou TXT local:

```powershell
radar import-profile config/professional_profile.example.yaml
radar profiles
radar show-profile 1
```

YAML/JSON devem conter dados estruturados:

- `profile_name`
- `headline`
- `summary`
- `skills`
- `experiences`
- `projects`
- `education`
- `languages`

Cada nova versao grava hash do arquivo, hash da estrutura validada e o caminho
local de origem. Reimportar o mesmo conteudo nao cria duplicata. Importar
conteudo diferente cria nova versao e torna essa versao ativa por padrao.

Na interface web, uploads seguem o mesmo modelo de validacao, mas nao preservam
o arquivo bruto nem caminho temporario. O servico recebe bytes validados, grava
hash/formato/origem sanitizada e descarta o conteudo original. A criacao manual
na web usa `ProfessionalProfileInput` diretamente e nao cria JSON em
`data/imports`.

```powershell
radar activate-profile 1
```

Apenas uma versao de perfil pode ficar ativa globalmente. Ativar uma versao
desativa qualquer outra versao ativa, mesmo que ela pertenca a outro perfil, e
registra auditoria local da troca.

O `Resume` base e reutilizado por perfil. Novos arquivos ou conteudos geram
novas `ResumeVersion`, sem criar uma entidade base duplicada para o mesmo
perfil e sem copiar o curriculo real para o repositorio.

## Evidencias

Habilidades podem ter evidencias diretas e tambem evidencias derivadas de
experiencias ou projetos. O sistema nunca inventa competencia: uma habilidade
citada sem evidencia aparece como `NOT_PROVEN`, nao como `MATCHED`.

Nivel informado na habilidade ajuda a avaliar requisitos de senioridade quando
existe evidencia para a habilidade, mas o nivel sozinho nao prova dominio.
Quando a interface mostra "Habilidade informada, mas ainda sem evidencia
associada.", a habilidade permanece declarada ate que o usuario associe
experiencia, projeto, curso, idioma ou evidencia direta.

Requisitos extraidos de listas de tecnologias da vaga entram como
`RequirementKind.UNKNOWN` quando a vaga nao deixa claro se sao obrigatorios ou
desejaveis. Isso evita tratar uma lista solta de ferramentas como eliminatoria.

## Comparacao

```powershell
radar compare-profile 123
radar show-compatibility 123
```

A comparacao grava:

- vaga;
- versao do perfil usada;
- score geral;
- requisitos obrigatorios e desejaveis;
- evidencias usadas;
- pontos de atencao;
- explicacao por requisito.

Cada `JobRequirementMatch` tambem preserva auditoria detalhada do requisito:
origem (`requirement_source`), texto original, termos avaliados e resultado por
termo em JSON. Registros antigos sem esses campos continuam legiveis como
historico legado.

Reexecutar a mesma comparacao para a mesma vaga, versao do perfil, versao das
regras e hash do conteudo retorna o registro existente. Quando a vaga, o perfil
ou as regras mudam, o Radar cria uma nova comparacao e preserva a anterior para
auditoria.

Categorias:

- `MATCHED`: requisito atendido com evidencia.
- `PARTIAL`: competencia proxima, nivel insuficiente ou evidencia incompleta.
- `NOT_PROVEN`: o perfil nao comprova a competencia, sem afirmar que o usuario
  nao a possui.
- `NOT_MATCHED`: requisito obrigatorio sem evidencia estruturada.
- `AMBIGUOUS`: requisito generico ou pouco verificavel que exige revisao humana.

Para estagio, a pontuacao evita filtro excessivamente rigido. Diferenciais
ausentes nao eliminam automaticamente a vaga; eles entram como evidencia ausente
ou ponto de revisao.

Regras especificas:

- Requisitos compostos de tecnologia, como `SQL e Python`, so ficam `MATCHED`
  quando todos os termos internos tiverem evidencia suficiente.
- Requisitos compostos com niveis especificos, como `SQL intermediario e Excel
  avancado`, avaliam cada termo com seu proprio nivel quando o texto permite.
- Requisitos compostos com nivel ambiguo ficam em revisao humana quando nao e
  seguro atribuir o nivel a um termo especifico.
- Nivel declarado e comparado quando o texto informa senioridade tecnica, por
  exemplo Excel avancado contra Excel basico.
- Formacao considera curso, situacao de estudante/cursando e previsao de
  conclusao quando esses dados existem.
- Experiencia com anos exige datas suficientes para comprovar a duracao. Dados
  incompletos ficam `AMBIGUOUS` em vez de inventar tempo de experiencia.
- Projetos e experiencias podem comprovar requisitos tecnicos quando citam a
  tecnologia ou atividade exigida.

## Comparacao em Lote

A interface web expoe a acao "Analisar compatibilidade das vagas" para vagas
`ELIGIBLE`, `RECOMMENDED` e `PENDING_REVIEW`. Ela exige um perfil ativo, respeita
limite informado, reutiliza comparacoes historicas com a mesma identidade e
reporta criadas, reutilizadas, ignoradas e falhas. A acao nao roda
automaticamente no carregamento do dashboard.
