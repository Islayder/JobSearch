# Máquinas de Estado

## Vaga

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> PENDING_REVIEW: regra inconclusiva
    NEW --> ELIGIBLE: elegível sem recomendação
    NEW --> RECOMMENDED: elegível com nota alta
    NEW --> ARCHIVED: incompatível
    PENDING_REVIEW --> ELIGIBLE: revisão aprovada
    PENDING_REVIEW --> DISMISSED: revisão descartada
    ELIGIBLE --> SEEN: visualizada
    ELIGIBLE --> DISMISSED: descartada
    RECOMMENDED --> SEEN: visualizada
    RECOMMENDED --> APPLIED: candidatura registrada
    APPLIED --> CLOSED: processo encerrado
    ARCHIVED --> [*]
    EXPIRED --> [*]
```

`DISMISSED` e `ARCHIVED` não voltam ao ranking automaticamente. Quando houver
candidatura prévia, a vaga passa a ser acompanhada como histórico.

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

A candidatura automática é proibida nesta versão. O estado existe para rastrear
ações humanas e preparar futuras integrações controladas.
