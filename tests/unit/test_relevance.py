from __future__ import annotations

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


def test_relevance_adjacent_credit_risk_crm_planning_people_analytics() -> None:
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

    assert result.status in {RelevanceStatus.MANUAL_REVIEW, RelevanceStatus.ADJACENT}
    assert "explanation" in result.reason


def _evaluate(title: str, description: str):
    return evaluate_role_relevance(
        RoleRelevanceInput(title=title, description=description),
        RelevanceRulesConfig(),
    )
