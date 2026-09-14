from packages.core.urls import canonical_source_url

def test_canonical_source_url_olx_regional():
    url = "https://sp.olx.com.br/sao-paulo-e-regiao/games/consoles-de-video-game/moleza-lacrado-playstation-5-01-digital-e-01-com-leitor-1530897984"
    assert canonical_source_url(url) == "https://www.olx.com.br/vi/1530897984"

def test_canonical_source_url_olx_already_canonical():
    url = "https://www.olx.com.br/vi/1530897984"
    assert canonical_source_url(url) == "https://www.olx.com.br/vi/1530897984"

def test_canonical_source_url_olx_with_query_params():
    url = "https://df.olx.com.br/distrito-federal-e-regiao/games/consoles-de-video-game/ps5-slim-1531118158?rec_detail_location=listing_no_result"
    assert canonical_source_url(url) == "https://www.olx.com.br/vi/1531118158"

def test_canonical_source_url_non_olx():
    url = "https://loja.lenovo.com/notebook-loq-15"
    assert canonical_source_url(url) == "https://loja.lenovo.com/notebook-loq-15"

def test_canonical_source_url_empty_or_none():
    assert canonical_source_url(None) == ""
    assert canonical_source_url("") == ""
