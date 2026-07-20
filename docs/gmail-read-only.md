# Gmail Somente Leitura

A integracao Gmail e opcional, local, desativada por padrao e limitada ao
escopo `https://www.googleapis.com/auth/gmail.readonly`.

## Configuracao

O arquivo versionado e apenas `config/gmail.example.yaml`. Para uso local,
crie `config/gmail.local.yaml`, que e ignorado pelo Git.

Campos:

- `enabled`: habilita a leitura quando `true`.
- `query`: consulta usada para procurar mensagens de candidaturas.
- `max_results`: limite por sincronizacao.
- `credentials_path`: caminho local para credenciais, fora do Git.
- `token_path`: caminho local para token revogavel, fora do Git.
- `scopes`: deve conter somente `gmail.readonly`.

Credenciais e tokens devem ficar fora do Git, preferencialmente em
`data/personal/gmail/` ou fora do diretorio do projeto. O sistema rejeita
caminhos em `config/` para credenciais reais.

## Comportamento

Quando ativado, o Radar pode pesquisar mensagens, ler assunto, remetente, data
e um trecho necessario do corpo. As mensagens sao salvas em `EmailMessage` com
origem `gmail`, trecho limitado e sugestao em JSON.

O Radar pode sugerir:

- confirmacao recebida;
- teste;
- case;
- entrevista;
- rejeicao;
- oferta;
- atualizacao de processo.

Toda sugestao exige revisao humana. A sincronizacao nao envia, responde,
encaminha, apaga, arquiva, marca como lida, altera labels, cria candidatura,
muda status ou cria evento automaticamente.

## Testes

Testes usam mensagens sinteticas e cliente fake. Nenhum teste acessa Gmail real,
rede externa, credenciais, tokens ou mensagens pessoais.
