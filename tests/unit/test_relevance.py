from __future__ import annotations

import pytest

from radar_vagas.config.schemas import RelevanceRulesConfig
from radar_vagas.domain.enums import RelevanceStatus
from radar_vagas.relevance.service import RoleRelevanceInput, evaluate_role_relevance


def test_relevance_core_data_bi_sql_python_software_and_automation() -> None:
    cases = [
        ("Analista de Dados Junior", "SQL e dashboards"),
        ("Estagio Business Intelligence", "Power BI"),
        ("Pessoa Desenvolvedora Junior", "Python e APIs"),
        ("Estagio em Automacao", "testes automatizados e tecnologia"),
    ]
    for title, description in cases:
        result = _evaluate(title, description)
        assert result.status is RelevanceStatus.CORE
        assert result.score > 0
        assert result.reason["core_matches"] or result.reason["technology_matches"]


def test_relevance_strong_adjacent_credit_risk_crm_planning_people_analytics() -> None:
    cases = [
        ("Estagio Credito", "indicadores e relatorios"),
        ("Estagio Risco", "analise de riscos"),
        ("Estagio CRM", "performance de campanhas"),
        ("Estagio People Analytics", "indicadores de pessoas"),
        ("Estagio Planejamento", "processos e dashboards"),
    ]
    for title, description in cases:
        assert _evaluate(title, description).status in {
            RelevanceStatus.ADJACENT,
            RelevanceStatus.CORE,
        }


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Estagio Operacoes", "apoio a areas internas"),
        ("Estagio Processos", "documentacao e tarefas administrativas"),
        ("Estagio Financas", "contas a pagar e rotinas internas"),
        ("Vendas com CRM", "uso operacional de CRM sem analise"),
    ],
)
def test_relevance_contextual_adjacent_without_support_is_not_auto_approved(
    title: str,
    description: str,
) -> None:
    result = _evaluate(title, description)

    assert result.status in {RelevanceStatus.MANUAL_REVIEW, RelevanceStatus.UNRELATED}


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Estagio Operacoes", "SQL, indicadores e Power BI"),
        ("Estagio Processos", "automacao, metricas e dashboards"),
        ("Estagio Financas", "analise financeira, indicadores e BI"),
        ("CRM", "segmentacao, metricas, campanhas e analise"),
    ],
)
def test_relevance_contextual_adjacent_with_support_is_relevant(
    title: str,
    description: str,
) -> None:
    result = _evaluate(title, description)

    assert result.status in {RelevanceStatus.ADJACENT, RelevanceStatus.CORE}


def test_relevance_negative_terms_win_over_weak_technical_signal() -> None:
    result = _evaluate("Estagio Administrativo", "SQL para cadastro de dados")

    assert result.status is RelevanceStatus.UNRELATED
    assert result.reason["negative_matches"]


def test_relevance_uses_word_boundaries_for_short_terms() -> None:
    false_positive = _evaluate("Estagio em Cambio", "apoio comercial")
    bi_match = _evaluate("Estagio BI", "dashboards")
    qa_false_positive = _evaluate("Estagio em Aquarela", "apoio criativo")
    qa_match = _evaluate("Estagio QA", "testes automatizados")

    assert false_positive.status is RelevanceStatus.UNRELATED
    assert "BI" not in false_positive.reason["core_matches"].get("title", [])
    assert bi_match.status is RelevanceStatus.CORE
    assert qa_false_positive.status is RelevanceStatus.UNRELATED
    assert "QA" not in qa_false_positive.reason["core_matches"].get("title", [])
    assert qa_match.status is RelevanceStatus.CORE


def test_relevance_matches_accents_and_plural_terms() -> None:
    accented = _evaluate("Est\u00e1gio de Cr\u00e9dito", "indicadores e relat\u00f3rios")
    plural = _evaluate("Estagio de Riscos", "analise de riscos")

    assert accented.status is RelevanceStatus.ADJACENT
    assert plural.status is RelevanceStatus.ADJACENT


def test_relevance_unrelated_for_admin_data_entry_sales_and_reception() -> None:
    cases = [
        ("Digitacao de Dados", "cadastro de dados"),
        ("Auxiliar Administrativo", "rotina administrativa"),
        ("Vendas Internas", "atendimento ao cliente"),
        ("Recepcao", "atendimento presencial"),
    ]
    for title, description in cases:
        assert _evaluate(title, description).status is RelevanceStatus.UNRELATED


def test_relevance_manual_review_for_weak_or_conflicting_signal() -> None:
    result = _evaluate("Estagio Operacoes", "apoio a areas internas")

    assert result.status in {RelevanceStatus.MANUAL_REVIEW, RelevanceStatus.UNRELATED}
    assert "explanation" in result.reason


def _evaluate(title: str, description: str):
    return evaluate_role_relevance(
        RoleRelevanceInput(title=title, description=description),
        RelevanceRulesConfig(),
    )
