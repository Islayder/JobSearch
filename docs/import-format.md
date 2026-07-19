# Formato de Importação

Radar de Vagas importa arquivos locais `.json` e `.csv` pelo comando:

```powershell
radar validate-file data/fixtures/import-example.json
radar import-file data/fixtures/import-example.csv --dry-run
radar import-file data/fixtures/import-example.csv --delimiter ";"
radar import-file data/fixtures/import-example.json --report data/exports/import-report.json
```

## Schema

Campos aceitos:

- `source_name`, obrigatório
- `source_type`
- `external_id`
- `url`
- `title`, obrigatório
- `company`, obrigatório
- `location`
- `description`
- `published_at`, ISO 8601
- `expires_at`, ISO 8601
- `employment_type`
- `work_model`
- `country`
- `state`
- `city`
- `remote_country_scope`
- `hours_per_day`
- `hours_per_week`
- `salary_min`
- `salary_max`
- `salary_period`
- `currency`
- `benefits`
- `application_url`
- `metadata`, objeto JSON

`url` e `description` podem ficar vazios em importação manual. Campos vazios no
CSV viram `null`. Colunas extras em CSV são preservadas em `metadata`.

## JSON

Lista direta:

```json
[
  {
    "source_name": "Importação manual",
    "title": "Estágio em Dados",
    "company": "Empresa X"
  }
]
```

Envelope:

```json
{
  "schema_version": "1.0",
  "items": [
    {
      "source_name": "Importação manual",
      "title": "Estágio em Dados",
      "company": "Empresa X"
    }
  ]
}
```

Versões explicitamente diferentes de `1.0` são rejeitadas.

## CSV

O CSV deve ter cabeçalho. A leitura usa UTF-8 ou UTF-8 com BOM. Delimitadores
suportados: vírgula e ponto e vírgula. Sem `--delimiter`, a detecção é
conservadora pela primeira linha.

## Aliases

Aliases são insensíveis a caixa e acentos:

- `source`, `fonte` -> `source_name`
- `job_title`, `cargo`, `vaga` -> `title`
- `empresa`, `organization` -> `company`
- `localizacao`, `localização` -> `location`
- `descricao`, `descrição` -> `description`
- `link`, `job_url` -> `url`
- `modalidade`, `tipo_trabalho` -> `work_model`
- `tipo_vaga`, `senioridade` -> `employment_type`
- `cidade` -> `city`
- `estado` -> `state`
- `pais`, `país` -> `country`
- `bolsa`, `salario`, `salário` -> `salary_min`
- `beneficios`, `benefícios` -> `benefits`

Quando duas colunas mapeiam para o mesmo campo com valores conflitantes, o item
é inválido.

## Enumerações

Tipos de oportunidade:

- estágio, intern, internship, pessoa estagiária -> `INTERNSHIP`
- trainee, programa trainee -> `TRAINEE`
- júnior, junior, jr, analista junior -> `JUNIOR`
- bolsista, bolsa de inovação, scholarship -> `SCHOLARSHIP`
- desconhecidos -> `UNKNOWN`

Modalidades:

- remoto, remote, home office, 100% remoto -> `REMOTE`
- híbrido, hibrido, hybrid -> `HYBRID`
- presencial, onsite, on-site -> `ONSITE`
- termos conflitantes ou desconhecidos -> `UNKNOWN`

## Benefícios

`benefits` aceita lista JSON, texto separado por ponto e vírgula ou texto
separado por pipe.

## Dry-Run

`--dry-run` valida o arquivo, normaliza dados, detecta duplicatas existentes,
detecta duplicatas prováveis, aplica elegibilidade preliminar e não altera o
banco.

## Relatório

`--report` gera JSON com:

- arquivo de entrada
- início e fim
- modo dry-run
- resumo
- itens válidos
- itens inválidos
- duplicatas exatas
- duplicatas prováveis

Erros esperados não exibem traceback por padrão.
