import os
import re
import uuid

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.e2e
def test_web_smoke_against_running_stack():
    base_url = os.getenv("GRIDSCOUT_E2E_URL")
    if not base_url:
        pytest.skip("Set GRIDSCOUT_E2E_URL to run the browser E2E against a running stack")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        second_name = f"E2E {uuid.uuid4().hex[:8]}"
        created = page.request.post(
            f"{base_url.rstrip('/')}/api/v1/profiles",
            data={"name": second_name},
        )
        assert created.ok
        page.goto(base_url, wait_until="networkidle")
        expect(page.get_by_role("heading", name=re.compile("Perfis de Pesquisa|Escolha um perfil"))).to_be_visible()
        profile_buttons = page.get_by_role("region", name="Perfis de Pesquisa").locator("button[type='button']")
        expect(profile_buttons.first).to_be_visible()
        profiles = page.request.get(f"{base_url.rstrip('/')}/api/v1/profiles")
        assert profiles.ok
        first_profile = profiles.json()["items"][0]
        profile_buttons.first.click()
        expect(page.get_by_text("Market Radar")).to_be_visible()

        # The selected profile survives a browser reload using only its local
        # profile ID; the profile data continues to come from the API.
        page.reload(wait_until="networkidle")
        expect(page.get_by_label("Perfil ativo")).to_have_value(first_profile["id"])

        # A search saves a reusable definition, then merely navigates to a
        # preconfigured Pipeline. It never exposes an execution control here.
        page.get_by_role("link", name="Buscas").click()
        expect(page.get_by_role("heading", name="Buscas")).to_be_visible()
        expect(page.get_by_role("button", name=re.compile("Executar [Pp]ipeline"))).to_have_count(0)
        page.get_by_label("Mensagem da busca").fill("RTX 3080")
        page.get_by_role("button", name="Enviar").click()
        expect(page.get_by_text("Entendimento da busca")).to_be_visible()
        page.get_by_role("button", name="Salvar escopo").click()
        expect(page.get_by_role("button", name="Salvo")).to_be_visible()
        page.get_by_role("button", name="Abrir no Pipeline").click()
        expect(page.get_by_role("heading", name="Pipelines")).to_be_visible()
        expect(page.get_by_role("button", name=re.compile("Trigger Pipeline Run|Executar [Pp]ipeline"))).to_be_visible()
        expect(page.get_by_role("button").filter(has_text=re.compile("Modelo: RTX 3080"))).to_be_visible()
        page.get_by_label("Ritmo").select_option("high_volume")
        expect(page.get_by_label("Acessar anúncios nesta execução")).not_to_be_checked()
        page.get_by_role("button", name="Busca ampla").click()
        page.get_by_role("textbox", name="Consulta").fill("notebook")
        page.get_by_label("Duração em minutos").fill("30")
        page.get_by_label("Agressividade").select_option("intensive")
        execute = page.get_by_role("button", name="Executar pipeline")
        expect(execute).to_be_enabled()
        execute.click()
        expect(page.get_by_text("Pipeline enfileirado.")).to_be_visible()
        browser.close()
