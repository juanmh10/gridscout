from apps.browser_worker.main import (
    SearchRequest,
    _extract_offer_price,
    _find_json_ld_product,
    _is_recommendation_url,
    _line_value,
    _parse_price,
    _parse_seller_rating,
    _parse_location,
    _parse_result_count,
    _search_url,
)
from packages.pipeline.runner import _summary_matches_scope
from types import SimpleNamespace


def test_current_olx_search_contract_uses_category_and_recent_sort():
    request = SearchRequest(query="rtx 3080", category="gpu", limit=10, sort="recent")
    assert _search_url(request) == (
        "https://www.olx.com.br/informatica/placas-de-video?q=rtx%203080&sf=1"
    )
    assert _parse_result_count("1 - 50 de 399 resultados") == 399
    assert _parse_result_count("0 resultados") == 0
    assert _is_recommendation_url(
        "https://www.olx.com.br/vi/1464872899?rec_detail_location=listing_no_result&is_fallback=false"
    )
    assert not _is_recommendation_url(
        "https://df.olx.com.br/informatica/placas-de-video/rtx-3080-1464872899"
    )


def test_current_olx_detail_contract_reads_product_json_ld_not_recommendations():
    product = _find_json_ld_product([
        '{"@type":"BreadcrumbList","itemListElement":[]}',
        '{"@type":"Product","identifier":"1464872899",'
        '"name":"RTX 3080 Zotac", "description":"Placa<br>usada",'
        '"offers":{"price":2999}}',
    ])
    assert product["identifier"] == "1464872899"
    assert _extract_offer_price(product) == 2999.0


def test_current_olx_detail_extracts_location_and_labeled_attributes():
    lines = [
        "Localização",
        "Setor Sudoeste",
        "Brasília, DF, 70670201",
        "Categoria",
        "Placas De Vídeo",
        "Condição",
        "Usado - Excelente",
    ]
    assert _parse_location(lines) == ("Brasília", "DF")
    assert _line_value(lines, {"condição", "condicao"}) == "Usado - Excelente"


def test_current_olx_detail_reads_public_seller_rating_without_profile_navigation():
    assert _parse_seller_rating(["Avaliação do vendedor: 4,9 estrelas"]) == 4.9
    assert _parse_seller_rating(["Na OLX desde 2020"]) == 0.0


def test_saved_scope_rejects_related_models_and_accepts_canonical_variant():
    product = SimpleNamespace(model="RTX 3060", variant="12GB")
    scope = {"query": "NVIDIA GeForce RTX 3060 12GB"}
    assert _summary_matches_scope("RTX 3060 Yeston 12GB GDDR6", scope, product)
    assert not _summary_matches_scope("Placa de vídeo GTX 1060 3GB", scope, product)

    cpu = SimpleNamespace(model="Ryzen 5 5600X", variant=None)
    assert not _summary_matches_scope("RTX 3080 Zotac", {"query": "Ryzen 5 5600X"}, cpu)


def test_parse_price_extracts_formatted_and_card_text_prices():
    assert _parse_price("R$ 3.500") == (3500.0, "dom")
    assert _parse_price("R$ 3.199,90") == (3199.9, "dom")
    assert _parse_price("PlayStation 5 Slim 825GB R$ 3.400 Belo Horizonte - MG") == (3400.0, "dom")
    assert _parse_price("Sem preço anunciado") == (None, "missing")
    assert _parse_price("A combinar") == (None, "missing")
    assert _parse_price("Sob consulta") == (None, "missing")
    assert _parse_price("12x de R$ 250 ou R$ 2.500 à vista") == (2500.0, "dom")


def test_parse_location_handles_hyphenated_and_comma_formats():
    assert _parse_location(["Campinas - SP", "Vila Nova"]) == ("Campinas", "SP")
    assert _parse_location(["Belo Horizonte - MG"]) == ("Belo Horizonte", "MG")
    assert _parse_location(["Curitiba, PR, 80000000"]) == ("Curitiba", "PR")


