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
6. Relevancia: preencha departamento, area, requisitos, responsabilidades e
   tecnologias quando a fonte fornecer esses dados; dry-run e persistencia devem
   usar a mesma entrada canonica.
7. Validacao: item sem titulo, identidade ou URL publica valida deve virar erro
   recuperavel do item.
8. Fixtures: crie fixtures sinteticas offline compativeis com o contrato
   observado; nao versione respostas reais completas.
9. Incremental: somente board autoritativo completo pode fechar por ausencia.
   Queries e paginas individuais nao fecham nem transferem propriedade de escopo
   autoritativo.
10. Dominio: use allowlist explicita, nao substring; bloqueie redirects fora da
   allowlist.
11. SSRF: toda requisicao passa pelo cliente HTTP central.
12. Rede: respeite intervalo minimo por host, retries conservadores e orcamento
    global de planos quando houver varias consultas.
13. Dry-run: compare contagens antes/depois e garanta ausencia total de escrita.
14. Smoke: execute real apenas depois dos testes offline, com limite pequeno e
   sem candidatura.
15. Documentacao: registre host, caminho, parametros, formato, campos, limites,
   erros e riscos.
16. Aceite: rode pytest, Ruff, mypy, comandos de saude e revise diff para
   garantir que nao ha banco, relatorio real, curriculo ou dado pessoal
   versionado.
