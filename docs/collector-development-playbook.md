# Playbook de Desenvolvimento de Coletores

1. Reconhecimento publico: valide paginas e requisicoes GET/HEAD realmente
   abertas, sem credenciais, tokens, login, cookies autenticados ou POST.
2. Classificacao de autoridade: defina `AUTHORITATIVE_BOARD`,
   `DISCOVERY_QUERY` ou `SINGLE_PAGE` antes de persistir.
3. Identidade: escolha chave estavel de plataforma e nunca use titulo ou
   descricao como identidade.
4. Paginacao: documente parametros, pagina inicial, limites, total, repeticao e
   criterio de parada.
5. Mapeamento: mapeie somente campos observados; nao invente salario, jornada,
   pais, curso, modalidade ou escopo remoto.
6. Validacao: item sem titulo, identidade ou URL publica valida deve virar erro
   recuperavel do item.
7. Fixtures: crie fixtures sinteticas offline compativeis com o contrato
   observado; nao versione respostas reais completas.
8. Incremental: somente board autoritativo completo pode fechar por ausencia.
   Queries e paginas individuais nao fecham.
9. Dominio: use allowlist explicita, nao substring; bloqueie redirects fora da
   allowlist.
10. SSRF: toda requisicao passa pelo cliente HTTP central.
11. Dry-run: compare contagens antes/depois e garanta ausencia total de escrita.
12. Smoke: execute real apenas depois dos testes offline, com limite pequeno e
   sem candidatura.
13. Documentacao: registre host, caminho, parametros, formato, campos, limites,
   erros e riscos.
14. Aceite: rode pytest, Ruff, mypy, comandos de saude e revise diff para
   garantir que nao ha banco, relatorio real, curriculo ou dado pessoal
   versionado.
