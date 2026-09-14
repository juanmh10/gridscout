import re
from typing import Optional

def canonical_source_url(url: Optional[str]) -> str:
    """Normalize marketplace listing URLs to safe canonical access links.

    For OLX listings, maps regional/slugged subdomains (e.g. sp.olx.com.br/...)
    to the root canonical routing URL (https://www.olx.com.br/vi/{item_id}) to
    prevent Cloudflare regional WAF blocks (403 Forbidden) and stale slug errors.
    """
    if not url:
        return ""
    url_str = str(url).strip()
    if "olx.com.br" in url_str:
        # Match standard OLX ad ID at the end of the slug, inside /vi/, or parameter
        match = re.search(r"(?:-|/vi/|/anuncio/|id=)(\d{7,12})(?:\?|#|$|/)", url_str)
        if match:
            return f"https://www.olx.com.br/vi/{match.group(1)}"
    return url_str
