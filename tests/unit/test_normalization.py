from radar_vagas.canonicalization.normalize import (
    generate_content_hash,
    is_belo_horizonte,
    normalize_city,
    normalize_company_name,
    normalize_spaces,
    normalize_text,
    normalize_title,
    normalize_url,
    remove_accents,
)


def test_text_normalization_removes_accents_spaces_and_case() -> None:
    assert remove_accents("Engenharia de Dados") == "Engenharia de Dados"
    assert normalize_spaces("  uma   vaga\tboa  ") == "uma vaga boa"
    assert normalize_text("  Análise   de DADOS  ") == "analise de dados"


def test_company_name_removes_common_legal_suffixes() -> None:
    assert normalize_company_name("Banco Exemplo S.A.") == "banco exemplo"
    assert normalize_company_name("Grupo Teste LTDA") == "grupo teste"
    assert normalize_company_name("Companhia Demo EIRELI") == "companhia demo"


def test_title_and_url_normalization() -> None:
    assert normalize_title(" Analista  de Dados Júnior ") == "analista de dados junior"
    assert (
        normalize_url("HTTPS://Example.COM/jobs//123/?utm_source=x&b=2&a=1#frag")
        == "https://example.com/jobs/123?a=1&b=2"
    )


def test_belo_horizonte_variations_are_canonical() -> None:
    assert normalize_city("BH") == "Belo Horizonte"
    assert normalize_city("Belo Horizonte, MG") == "Belo Horizonte"
    assert normalize_city("Belo Horizonte - Minas Gerais") == "Belo Horizonte"
    assert is_belo_horizonte("Belo Horizonte", "MG")


def test_metropolitan_cities_are_not_belo_horizonte() -> None:
    assert normalize_city("Contagem") != "Belo Horizonte"
    assert normalize_city("Betim") != "Belo Horizonte"
    assert normalize_city("Nova Lima") != "Belo Horizonte"
    assert not is_belo_horizonte("Sabará", "MG")


def test_content_hash_is_deterministic() -> None:
    first = generate_content_hash(["Vaga", "Empresa", "Descrição"])
    second = generate_content_hash([" vaga ", "empresa", "descricao"])
    assert first == second
