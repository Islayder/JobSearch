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
- curriculos e resumes por padrao de nome;
- `config/profile.local.yaml`;
- `config/professional_profile.local.yaml`;
- demais `config/*.local.yaml` sensiveis;
- credenciais e tokens locais.

## Curriculos

Arquivos de curriculo pessoais devem ficar fora do Git. O sistema pode importar
um arquivo local estruturado, gravar hash e versao no banco local, mas nao
versiona o arquivo real, nao gera curriculo e nao envia documentos para
plataformas.

## Exportacoes

Relatorios em `data/exports/` podem conter dados operacionais ou pessoais. Eles
sao ignorados por padrao; compartilhe manualmente apenas quando necessario.
