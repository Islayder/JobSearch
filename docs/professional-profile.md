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

## Evidencias

Habilidades podem ter evidencias diretas e tambem evidencias derivadas de
experiencias ou projetos. O sistema nunca inventa competencia: uma habilidade
citada sem evidencia aparece como `NOT_PROVEN`, nao como `MATCHED`.

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
