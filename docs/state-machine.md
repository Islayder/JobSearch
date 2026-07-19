# Maquinas de Estado

## Publicacao

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> LINKED: associada a Job
    NEW --> PROBABLE_DUPLICATE: duplicata provavel
    NEW --> SKIPPED_DUPLICATE: duplicata exata
    LINKED --> LINKED: vista inalterada
    LINKED --> LINKED: conteudo alterado com revisao
    LINKED --> CLOSED: ausente apos limite configurado
    CLOSED --> LINKED: reaparecimento
```

`CLOSED` em `Posting` significa que a publicacao deixou de aparecer em snapshots
completos bem-sucedidos. Falhas, snapshots parciais e HTTP 304 nao fecham
publicacoes.

## Vaga

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> PENDING_REVIEW: regra inconclusiva
    NEW --> ELIGIBLE: elegivel sem recomendacao
    NEW --> RECOMMENDED: elegivel com nota alta
    NEW --> ARCHIVED: incompativel
    PENDING_REVIEW --> ELIGIBLE: revisao aprovada
    PENDING_REVIEW --> DISMISSED: revisao descartada
    ELIGIBLE --> SEEN: visualizada
    ELIGIBLE --> DISMISSED: descartada
    RECOMMENDED --> SEEN: visualizada
    RECOMMENDED --> APPLIED: candidatura registrada
    APPLIED --> CLOSED: processo encerrado
    ARCHIVED --> [*]
    CLOSED --> NEW: reaparecimento sem candidatura ou descarte humano
    EXPIRED --> [*]
```

`DISMISSED`, `APPLIED` e vagas com candidatura existente nao voltam ao ranking
automaticamente por causa de uma mudanca de publicacao. Quando houver
candidatura previa, a vaga passa a ser acompanhada como historico.

## Candidatura

```mermaid
stateDiagram-v2
    [*] --> PREPARING
    PREPARING --> AWAITING_REVIEW
    AWAITING_REVIEW --> READY
    READY --> SUBMITTED
    SUBMITTED --> TEST
    SUBMITTED --> INTERVIEW
    TEST --> INTERVIEW
    INTERVIEW --> FINAL_STAGE
    FINAL_STAGE --> OFFER
    SUBMITTED --> REJECTED
    TEST --> REJECTED
    INTERVIEW --> REJECTED
    OFFER --> CLOSED
    REJECTED --> CLOSED
    PREPARING --> WITHDRAWN
    WITHDRAWN --> CLOSED
```

A candidatura automatica e proibida nesta versao. O estado existe para rastrear
acoes humanas e preparar futuras integracoes controladas.
