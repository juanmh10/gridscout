"""Fallback helper to create an OLX Playwright storage state manually.

The normal flow is now driven by the Settings UI (email -> one-time code).
This helper remains as a local fallback for a manual browser session. It
intentionally does not accept, store, or submit credentials.
"""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    session_dir = Path(os.getenv("OLX_SESSION_DIR", ".cache/olx_session"))
    session_file = session_dir / "storage_state.json"
    session_dir.mkdir(parents=True, exist_ok=True)

    print("Abrindo o OLX em navegador visível.")
    print("Faça o login manualmente. Não informe senhas ao GridScout.")
    print("Quando a sessão estiver autenticada, volte ao terminal e pressione Enter.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.olx.com.br", wait_until="domcontentloaded", timeout=60000)
        input()
        context.storage_state(path=str(session_file))
        browser.close()

    print(f"Sessão salva em {session_file}")
    print("Escopo do worker: somente busca e detalhe de anúncio; nenhuma escrita é implementada.")


if __name__ == "__main__":
    main()
