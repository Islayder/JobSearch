# Importacao Revisada de Curriculo

O importador revisado permite usar PDF textual, DOCX, TXT ou Markdown como
entrada para criar uma nova versao de perfil profissional. O fluxo e local e
sempre exige revisao humana antes de criar ou ativar um perfil.

## Fluxo

1. A web recebe o arquivo em memoria, com limite de 8 MB.
2. O formato e validado por assinatura e estrutura, nao apenas por extensao.
3. O texto e extraido para blocos intermediarios com pagina, ordem, tipo e
   dica de secao. PDFs passam por avaliacao de qualidade por pagina antes de
   escolher a estrategia de texto.
4. Um parser deterministico cria candidatos isolados para revisao.
5. O usuario edita, confirma, remove ou restaura cada candidato em uma tela com
   indice por secao, resumo de pendencias e acoes fixas de confirmacao.
6. Somente itens confirmados ou editados entram em `ProfessionalProfileInput`.
7. A confirmacao valida o perfil pelo mesmo servico de dominio usado pelos
   demais fluxos e so ativa a versao quando a opcao "Ativar agora" estiver
   marcada.

GETs nunca alteram dados. Todas as acoes de upload, nova tentativa de
extracao, edicao, descarte, confirmacao e limpeza exigem CSRF.

## Interface Web

O upload usa uma dropzone grande com formatos, limite de 8 MB e aviso de
privacidade. A revisao organiza candidatos em Resumo, Experiencias, Projetos,
Formacao, Habilidades, Idiomas, Ambiguos e Avisos. A pagina tambem mostra modo
e qualidade da extracao. Quando um PDF textual fica degradado, a tela oferece
"Tentar outro modo de extracao" com os modos Automatico, Texto normal, Layout e
Geometrico; o usuario precisa reenviar o mesmo PDF e a acao continua sendo POST
com CSRF.

## Formatos

- PDF: deve iniciar com `%PDF-`, nao pode estar protegido por senha, precisa
  ter paginas e no maximo 30 paginas. Por pagina, o importador tenta texto
  normal, layout e, quando as duas primeiras estrategias ficam ruins, fallback
  geometrico por fragmentos e coordenadas. A escolha usa metricas como letras,
  palavras, proporcao de espacos, tamanho medio de tokens, tokens longos,
  linhas, headings, datas, separadores, incidencia de texto colado e caracteres
  imprimiveis. PDF sem texto extraivel retorna a mensagem "Não foi possível
  encontrar texto suficiente neste PDF." O sistema nao faz OCR.
- DOCX: deve ser ZIP valido com `[Content_Types].xml` e `word/document.xml`.
  Caminhos internos absolutos ou com traversal, macros, referencias externas,
  compressao abusiva, mais de 500 entradas ou mais de 25 MB descompactados sao
  rejeitados. Imagens e objetos incorporados sao ignorados.
- TXT/Markdown: devem estar em UTF-8, sem NUL e com texto suficiente para
  gerar revisao.

Arquivos `.doc` antigos e `.docm` com macro nao sao aceitos.

## Dados Gravados

O banco guarda `ResumeImportSession` com chave aleatoria, formato, nome
sanitizado, hash do conteudo, modo de extracao, qualidade, metricas agregadas
sanitizadas, contadores e status. Cada `ResumeImportCandidate` guarda tipo,
decisao, carga estruturada revisavel, confianca, explicacao e trecho curto de
origem. O arquivo bruto, bytes originais, caminho temporario e texto completo
extraido nao sao persistidos.

Quando a importacao e confirmada, a versao de perfil resultante recebe a origem
`Curriculo PDF`, `Curriculo DOCX`, `Curriculo TXT` ou `Curriculo Markdown` e o
JSON bruto validado preserva a proveniencia dos itens aceitos.

## Regras de Interpretacao

O parser nao inventa informacoes ausentes. Linhas de contato e cabecalhos que
parecem nome/localizacao nao viram titulo profissional. Uma linha curta que
corresponde exatamente a um alias de secao vira heading mesmo quando o extrator
original a marcou como paragrafo. Experiencias, projetos e formacoes podem
agrupar multiplas linhas e bullets, preservando todos os `block_ids` usados.
Habilidades declaradas em secao de habilidades continuam declaradas ate serem
confirmadas; evidencias sao associadas apenas quando a habilidade aparece em
experiencia, projeto, formacao ou outro item estruturado revisado. Trechos
ambiguous ficam em uma secao propria para leitura humana e nao entram no perfil
automaticamente.

Mensagens de erro devem ser compreensiveis para o usuario e nao expor caminhos
locais, bytes, XML interno, nomes de bibliotecas, traceback ou detalhes de
tabela.
