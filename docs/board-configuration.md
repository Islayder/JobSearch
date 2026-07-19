# Configuracao de Boards

Boards versionados ficam em:

```text
config/company_boards.yaml
```

O arquivo real local pode ficar em:

```text
config/company_boards.local.yaml
```

Esse override local e ignorado pelo Git. Quando a mesma `key` aparece no arquivo
local, os campos locais substituem os campos versionados.

## Formato

```yaml
boards:
  - key: empresa-exemplo-greenhouse
    company_name: Empresa Exemplo
    collector: greenhouse
    board_token: empresaexemplo
    enabled: true
    priority: 100
    tags:
      - technology
      - remote
    notes: Board ficticio para demonstrar o formato
```

Lever:

```yaml
boards:
  - key: empresa-exemplo-lever
    company_name: Empresa Exemplo
    collector: lever
    board_token: empresaexemplo
    enabled: false
```

Pagina JSON-LD:

```yaml
boards:
  - key: vaga-exemplo-jobposting
    company_name: Empresa Exemplo
    collector: jobposting
    url: https://example.invalid/jobs/123
    enabled: false
```

## Regras

- `key` deve ser unica dentro de cada arquivo.
- `collector` deve ser `jobposting`, `greenhouse` ou `lever`.
- `greenhouse` e `lever` exigem `board_token`.
- `jobposting` exige `url`.
- `enabled: false` remove o board de `collect-all`.
- `tags` sao normalizadas em minusculo.
- Nenhum segredo deve ser armazenado.

## Comandos

```powershell
radar boards
radar boards --collector greenhouse
radar boards --enabled
radar boards --tag remote
radar show-board empresa-exemplo-greenhouse
radar collect-board empresa-exemplo-greenhouse --dry-run
radar collect-all --collector greenhouse --tag remote --dry-run
```

Coleta direta sem YAML:

```powershell
radar collect-board greenhouse --board-token empresa --company "Empresa" --dry-run
radar collect-board lever --board-token empresa --company "Empresa" --dry-run
```

Para persistir um board direto no banco, use `--save-board`; isso nao altera YAML:

```powershell
radar collect-board greenhouse --board-token empresa --company "Empresa" --save-board empresa-greenhouse
```
