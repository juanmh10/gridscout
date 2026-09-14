import json
import logging
import os
import re
import asyncio
import datetime as dt
import base64
import signal
import subprocess
import html as html_lib
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from packages.marketplace.olx_guard import OlxGuardError, OlxNavigationGuard


SESSION_FILE = Path(os.getenv("OLX_SESSION_DIR", ".cache/olx_session")) / "storage_state.json"
SESSION_METADATA_FILE = SESSION_FILE.with_name("session_metadata.json")
RATE_LIMIT_STATE_FILE = SESSION_FILE.with_name("rate_limit_state.json")
OLX_BASE_URL = "https://www.olx.com.br"
OLX_ACCOUNT_URL = "https://conta.olx.com.br/"
CDP_ENDPOINT = os.getenv("OLX_CDP_ENDPOINT", "").strip().rstrip("/")
CHROME_BINARY = os.getenv("OLX_CHROME_BINARY", "google-chrome")
CHROME_PROFILE_DIR = Path(
    os.getenv("OLX_CHROME_PROFILE_DIR", ".cache/olx_browser_profile")
)
CHROME_LOG_FILE = Path(
    os.getenv("OLX_CHROME_LOG_FILE", ".cache/gridscout-olx-chrome.log")
)
CHROME_START_TIMEOUT = float(os.getenv("OLX_CHROME_START_TIMEOUT", "15"))
SESSION_PROBE_CACHE_SECONDS = float(os.getenv("OLX_SESSION_PROBE_CACHE_SECONDS", "30"))
OLX_PARSER_VERSION = "olx-dom-2026-08-24"
OLX_NAV_TIMEOUT_MS = int(os.getenv("OLX_NAV_TIMEOUT_MS", "45000"))
OLX_RESULT_WAIT_MS = int(os.getenv("OLX_RESULT_WAIT_MS", "12000"))

logger = logging.getLogger(__name__)

app = FastAPI(title="GridScout Read-only Browser Worker", version="1.0.0")


# The login browser must stay alive between the email and one-time-code
# requests. The email is held only until OLX confirms the resulting session;
# OTP values are never persisted or logged.
_login_lock = asyncio.Lock()
_login_playwright = None
_login_browser = None
_login_context = None
_login_page = None
_login_cdp = False
_login_stage = "idle"
_login_message = ""
_last_error_code = None
_last_error_message = ""
_pending_login_email = ""
_chrome_launch_lock = asyncio.Lock()
_chrome_process = None
_chrome_log_handle = None
_session_probe_lock = asyncio.Lock()
_session_probe_cache = None
_session_probe_cached_at = 0.0
_olx_guard = OlxNavigationGuard(RATE_LIMIT_STATE_FILE)


class BrowserWorkerError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _detail(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _guard_detail(error: OlxGuardError) -> dict:
    return {"code": error.code, **error.detail}


def _set_error(code: str | None, message: str = "") -> None:
    global _last_error_code, _last_error_message
    _last_error_code = code
    _last_error_message = message


def _login_headless() -> bool:
    return os.getenv("OLX_LOGIN_HEADLESS", "false").lower() in {"1", "true", "yes"}


class SearchRequest(BaseModel):
    query: str
    category: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    olx_pay_only: bool = False
    delivery_only: bool = False
    require_price: bool = False
    limit: int = 20
    sort: str = "recent"
    page: int = 1


class DetailRequest(BaseModel):
    external_id: str


class LoginStartRequest(BaseModel):
    email: str


class LoginCodeRequest(BaseModel):
    code: str


def _session_data_available() -> bool:
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        return bool(data.get("cookies"))
    except (OSError, ValueError):
        return False


def _cached_cookie_count() -> int:
    try:
        return len(json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("cookies", []))
    except (OSError, ValueError):
        return 0


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_session_metadata() -> dict:
    try:
        data = json.loads(SESSION_METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_session_metadata(metadata: dict) -> None:
    SESSION_METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = SESSION_METADATA_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temp_file, 0o600)
    temp_file.replace(SESSION_METADATA_FILE)
    os.chmod(SESSION_METADATA_FILE, 0o600)


def _is_browser_unavailable_code(code: str | None) -> bool:
    normalized = str(code or "").strip().lower()
    return normalized == "olx_session_probe_failed" or normalized.startswith("olx_browser")


def _is_browser_transport_error(error: BaseException) -> bool:
    error_text = f"{type(error).__name__}: {error}".casefold()
    return any(
        marker in error_text
        for marker in (
            "targetclosederror",
            "target page, context or browser has been closed",
            "browser has been closed",
            "browsercontext.new_page",
            "connection closed",
            "websocket is not open",
        )
    )


def _probe_is_browser_failure(probe: dict) -> bool:
    return probe.get("session_state") == "unavailable" or _is_browser_unavailable_code(
        probe.get("error_code")
    )


def _update_metadata_from_probe(probe: dict) -> None:
    metadata = _read_session_metadata()
    metadata.setdefault("schema_version", 1)
    metadata.setdefault("marketplace", "olx")
    browser_failure = _probe_is_browser_failure(probe)
    if browser_failure:
        # Keep the last confirmed authentication separate from the current
        # browser health. A crashed/closed Chrome must not erase a valid OLX
        # session and later be reported as an account logout.
        if metadata.get("last_session_state") in {None, "error", "unavailable"}:
            metadata["last_session_state"] = (
                "connected" if metadata.get("last_verified_at") else "unavailable"
            )
    else:
        metadata["last_session_state"] = probe.get("session_state")
    metadata.update(
        {
            "last_checked_at": probe.get("checked_at"),
            "last_probe_state": probe.get("session_state"),
            "last_error_code": probe.get("error_code"),
            "last_error_message": probe.get("error_message", ""),
        }
    )
    if probe.get("authenticated"):
        verified_at = probe.get("checked_at")
        metadata["last_verified_at"] = verified_at
        metadata.setdefault("authenticated_at", verified_at)
    _write_session_metadata(metadata)


def _invalidate_session_probe() -> None:
    global _session_probe_cache, _session_probe_cached_at
    _session_probe_cache = None
    _session_probe_cached_at = 0.0


def _login_in_progress() -> bool:
    return _login_stage in {"starting", "code_required", "manual_action_required", "manual_login"}


async def _cdp_ready() -> bool:
    if not CDP_ENDPOINT:
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{CDP_ENDPOINT}/json/version")
            response.raise_for_status()
            payload = response.json()
            return bool(payload.get("webSocketDebuggerUrl"))
    except Exception:
        return False


def _chrome_command() -> list[str]:
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return [
        CHROME_BINARY,
        "--remote-debugging-port=9222",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={CHROME_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]


async def _start_chrome() -> None:
    global _chrome_process, _chrome_log_handle
    if not CDP_ENDPOINT:
        return

    if await _cdp_ready():
        return

    async with _chrome_launch_lock:
        if await _cdp_ready():
            return

        CHROME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            _chrome_log_handle = CHROME_LOG_FILE.open("a", encoding="utf-8")
            _chrome_process = await asyncio.to_thread(
                subprocess.Popen,
                _chrome_command(),
                stdin=subprocess.DEVNULL,
                stdout=_chrome_log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except Exception as exc:
            logger.exception("Could not start the visible OLX Chrome")
            _set_error(
                "olx_browser_start_failed",
                "Não foi possível iniciar o Chrome visível da OLX.",
            )
            raise BrowserWorkerError(
                "olx_browser_start_failed",
                "Não foi possível iniciar o Chrome visível da OLX.",
            ) from exc

        deadline = asyncio.get_running_loop().time() + CHROME_START_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            if await _cdp_ready():
                _set_error(None)
                return
            if _chrome_process.poll() is not None:
                break
            await asyncio.sleep(0.5)

        logger.error("Visible OLX Chrome did not expose CDP within %.1fs", CHROME_START_TIMEOUT)
        _set_error(
            "olx_browser_unavailable",
            "O Chrome visível da OLX não ficou disponível.",
        )
        raise BrowserWorkerError(
            "olx_browser_unavailable",
            "O Chrome visível da OLX não ficou disponível.",
        )


async def _stop_managed_chrome() -> None:
    global _chrome_process, _chrome_log_handle
    process = _chrome_process
    if process is not None:
        try:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
            else:
                # Reap an already exited process so it cannot remain a zombie.
                process.poll()
        except (OSError, subprocess.TimeoutExpired):
            logger.debug("Could not fully stop the managed OLX Chrome", exc_info=True)
        finally:
            _chrome_process = None

    if _chrome_log_handle is not None:
        try:
            _chrome_log_handle.close()
        except OSError:
            logger.debug("Chrome log handle was already closed", exc_info=True)
        _chrome_log_handle = None


async def _restart_chrome() -> None:
    if not CDP_ENDPOINT:
        return
    async with _chrome_launch_lock:
        await _stop_managed_chrome()
        deadline = asyncio.get_running_loop().time() + 5
        while await _cdp_ready() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.2)
    await _start_chrome()


async def _ensure_browser() -> None:
    if CDP_ENDPOINT:
        await _start_chrome()


def _http_exception(error: BrowserWorkerError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail=_detail(error.code, error.message),
    )


def _guard_http_exception(error: OlxGuardError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=_guard_detail(error))


def _parse_price(text: str) -> tuple[float | None, str]:
    if not text:
        return None, "missing"
    text_lower = text.casefold()
    if any(p in text_lower for p in ("a combinar", "sob consulta", "gratis", "grátis", "troco", "permuta", "consulte")):
        return None, "missing"

    # Match cash/total prices, avoiding installments like "12x de R$ 250"
    matches = list(re.finditer(r"(?:^|[^\dxX])(?:R\$\s*|BRL\s*)([\d.]+(?:,[\d]{1,2})?)", text))
    if matches:
        prices = []
        for m in matches:
            try:
                val = float(m.group(1).replace(".", "").replace(",", "."))
                if val > 0:
                    prices.append(val)
            except (ValueError, TypeError):
                continue
        if prices:
            # If multiple prices found (e.g. cash price vs installment), prefer the primary/highest cash price
            return (prices[0] if len(prices) == 1 else max(prices)), "dom"

    match = re.search(r"R\$\s*([\d.]+(?:,[\d]{1,2})?)", text)
    if not match:
        return None, "missing"
    try:
        val = float(match.group(1).replace(".", "").replace(",", "."))
        return (val, "dom") if val > 0 else (None, "missing")
    except (ValueError, TypeError):
        return None, "missing"


def _sanitize_snippet(text: str) -> str:
    if not text:
        return ""
    # Redact potential emails
    sanitized = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL_REDACTED]", text)
    # Redact potential phone numbers
    sanitized = re.sub(r"\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}", "[PHONE_REDACTED]", sanitized)
    # Redact session / token strings
    sanitized = re.sub(r"(token|cookie|auth|session|key|secret)[=:\s]+[A-Za-z0-9_\-\.]{10,}", r"\1=[REDACTED]", sanitized, flags=re.IGNORECASE)
    return sanitized[:2048]


def _parse_result_count(text: str) -> int:
    match = re.search(r"de\s+([\d.]+)\s+resultados", text or "", re.IGNORECASE)
    if match:
        return int(match.group(1).replace(".", ""))
    if re.search(r"\b0\s+resultados?\b|nenhum resultado", text or "", re.IGNORECASE):
        return 0
    return 0


def _is_recommendation_url(url: str) -> bool:
    parsed = urlparse(url)
    params = {key.casefold(): [value.casefold() for value in values] for key, values in parse_qs(parsed.query).items()}
    return (
        "rec_detail_location" in params
        or "rec_detail_recommendation" in params
        or "rec_detail_engine" in params
        or params.get("is_fallback") == ["true"]
    )


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value or "")).strip()


def _clean_html(value: str | None) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\n{3,}", "\n\n", html_lib.unescape(text)).strip()


def _search_url(request: SearchRequest) -> str:
    category_paths = {
        "gpu": "/informatica/placas-de-video",
        "notebook": "/informatica/notebooks",
        "cpu": "/informatica/pecas-de-hardware",
        "ram": "/informatica/pecas-de-hardware",
        "ssd": "/informatica/pecas-de-hardware",
        "motherboard": "/informatica/pecas-de-hardware",
    }
    path = category_paths.get((request.category or "").strip().lower(), "/brasil")
    params = f"q={quote(request.query)}"
    if (request.sort or "recent").lower() in {"recent", "newest", "mais_recentes"}:
        params += "&sf=1"
    if request.min_price is not None and request.min_price > 0:
        params += f"&ps={int(request.min_price)}"
    elif request.require_price:
        params += "&ps=10"
    if request.max_price is not None and request.max_price > 0:
        params += f"&pe={int(request.max_price)}"
    if request.olx_pay_only or request.delivery_only:
        params += "&op=1"
    if request.page > 1:
        params += f"&o={request.page}"
    return f"{OLX_BASE_URL}{path}?{params}"


def _find_json_ld_product(payloads: list[str]) -> dict:
    def visit(value):
        if isinstance(value, list):
            for item in value:
                found = visit(item)
                if found:
                    return found
        if isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Product" in types:
                return value
            for key in ("@graph", "mainEntity", "item"):
                found = visit(value.get(key))
                if found:
                    return found
        return None

    for raw in payloads:
        try:
            found = visit(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if found:
            return found
    return {}


def _extract_offer_price(product: dict) -> float:
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return 0.0
    raw = offers.get("price", 0) or 0
    if isinstance(raw, (int, float)):
        return float(raw)
    value = str(raw).strip()
    try:
        return float(value.replace(".", "").replace(",", ".")) if "," in value else float(value)
    except (TypeError, ValueError):
        return 0.0


def _line_value(lines: list[str], labels: set[str], limit: int = 3) -> str:
    normalized = [line.strip() for line in lines if line.strip()]
    for index, line in enumerate(normalized):
        if line.casefold().rstrip(":") in labels:
            for candidate in normalized[index + 1:index + 1 + limit]:
                if candidate.casefold().rstrip(":") not in labels:
                    return candidate
    return ""


def _parse_location(lines: list[str]) -> tuple[str, str]:
    for line in lines:
        cleaned = _clean_text(line)
        match = re.search(r"([^,/]+?)\s*[-,\/]\s*([A-Z]{2})(?:[,\s\d]|$)", cleaned)
        if match:
            city_cand = _clean_text(match.group(1))
            uf_cand = match.group(2)
            if len(city_cand) >= 2 and not city_cand.startswith("R$") and not city_cand.casefold().startswith("foto de"):
                return city_cand, uf_cand
    return "", ""


def _page_evidence_lines(lines: list[str], markers: tuple[str, ...]) -> list[str]:
    return [
        _clean_text(line)
        for line in lines
        if any(marker in _clean_text(line).casefold() for marker in markers)
    ][:8]


def _parse_seller_rating(lines: list[str]) -> float:
    for line in lines:
        normalized = _clean_text(line)
        if not re.search(r"avalia|estrel|nota", normalized, re.IGNORECASE):
            continue
        match = re.search(r"\b([0-5](?:[,.]\d)?)\b", normalized)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if 0.0 <= value <= 5.0:
            return value
    return 0.0


async def _extract_page_photos(page) -> list[dict]:
    """Capture only images rendered by the current listing page.

    This helper never follows an image URL or creates a new page. Gallery
    controls are optional and restricted to explicit next/previous buttons.
    The bytes are returned transiently to the model gateway and are not
    persisted in the listing record.
    """
    photos: list[dict] = []
    seen: set[str] = set()
    selectors = (
        "[data-testid*='gallery'] img",
        "[data-testid*='image'] img",
        "[data-testid*='photo'] img",
        "main img",
    )

    async def capture_visible_images() -> None:
        for selector in selectors:
            locator = page.locator(selector)
            count = min(await locator.count(), 12)
            for index in range(count):
                image = locator.nth(index)
                try:
                    if not await image.is_visible():
                        continue
                    alt = _clean_text(await image.get_attribute("alt"))
                    if any(marker in alt.casefold() for marker in ("perfil", "avatar", "foto de")):
                        continue
                    if await image.evaluate(
                        "element => Boolean(element.closest('[class*=seller], [class*=profile], [data-testid*=seller], [data-testid*=profile]'))"
                    ):
                        continue
                    src = await image.get_attribute("src") or await image.get_attribute("data-src") or ""
                    key = src or f"{selector}:{index}"
                    if key in seen:
                        continue
                    screenshot = await image.screenshot(type="jpeg", quality=72)
                    photos.append({
                        "url": src,
                        "alt": alt,
                        "mime_type": "image/jpeg",
                        "data_url": "data:image/jpeg;base64," + base64.b64encode(screenshot).decode("ascii"),
                    })
                    seen.add(key)
                    if len(photos) >= 5:
                        return
                except Exception:
                    continue
            if len(photos) >= 5:
                return

    await capture_visible_images()
    if len(photos) < 5:
        next_buttons = page.locator(
            "button[aria-label*='Próxima'], button[aria-label*='proxima'], "
            "button[aria-label*='Next'], button[data-testid*='gallery-next']"
        )
        for _ in range(4):
            if not await next_buttons.count():
                break
            try:
                await next_buttons.first.click(timeout=1000)
                await page.wait_for_timeout(120)
            except Exception:
                break
            await capture_visible_images()
            if len(photos) >= 5:
                break
    return photos[:5]


def _page_blocked(body: str) -> bool:
    text = (body or "").casefold()
    return any(
        marker in text
        for marker in (
            "verifique se você é humano",
            "verifique se voce e humano",
            "captcha",
            "acesso negado",
            "request blocked",
            "too many requests",
            "cloudflare",
            "attention required",
        )
    )


def _external_id(url: str) -> str:
    # Keep the canonical URL as the identifier. It lets the detail request be
    # read-only and avoids guessing OLX's evolving numeric/slug URL format.
    return url


async def _new_context(playwright):
    if not _session_data_available():
        raise HTTPException(
            status_code=401,
            detail=_detail(
                "olx_session_missing",
                "Nenhuma sessão OLX somente leitura está disponível.",
            ),
        )
    if CDP_ENDPOINT:
        last_error = None
        for attempt in range(2):
            browser = None
            try:
                await _ensure_browser()
                browser = await playwright.chromium.connect_over_cdp(CDP_ENDPOINT)
                if not browser.contexts:
                    raise RuntimeError("The normal WSL Chrome has no browser context")
                context = browser.contexts[0]

                # Validate the CDP context before handing it to a caller. A
                # browser can still answer /json/version while its default
                # context has already been closed.
                probe_page = await context.new_page()
                await probe_page.close()
                return browser, context, True
            except BrowserWorkerError as exc:
                raise _http_exception(exc) from exc
            except Exception as exc:
                last_error = exc
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        logger.debug("Could not disconnect closed OLX Chrome", exc_info=True)
                if attempt == 0:
                    logger.warning("OLX Chrome CDP target closed; restarting the managed browser")
                    try:
                        await _restart_chrome()
                    except BrowserWorkerError as restart_error:
                        raise _http_exception(restart_error) from exc
                    continue

        logger.error("Could not connect to visible OLX Chrome over CDP: %s", last_error)
        _set_error(
            "olx_browser_unavailable",
            "O Chrome visível da OLX está indisponível. Tente novamente.",
        )
        raise _http_exception(
            BrowserWorkerError(
                "olx_browser_unavailable",
                "O Chrome visível da OLX está indisponível. Tente novamente.",
            )
        ) from last_error
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(storage_state=str(SESSION_FILE))
    return browser, context, False


async def _new_worker_page(context):
    """A dedicated tab for automated reads; never touch user/login tabs."""
    try:
        page = await context.new_page()
    except Exception as exc:
        if CDP_ENDPOINT and _is_browser_transport_error(exc):
            raise _http_exception(
                BrowserWorkerError(
                    "olx_browser_unavailable",
                    "O Chrome visível da OLX está indisponível. Tente novamente.",
                )
            ) from exc
        raise
    setattr(page, "_gridscout_worker_owned", True)
    return page


async def _close_worker_page(page) -> None:
    if page is not None and getattr(page, "_gridscout_worker_owned", False) and not page.is_closed():
        try:
            await page.close()
        except Exception:
            logger.debug("OLX worker tab was already closed", exc_info=True)


async def _close_login_browser() -> None:
    global _login_playwright, _login_browser, _login_context, _login_page, _login_cdp
    if _login_cdp and _login_page is not None and not _login_page.is_closed():
        try:
            await _login_page.close()
        except Exception:
            logger.debug("Login page was already closed", exc_info=True)
    if not _login_cdp:
        if _login_context is not None:
            await _login_context.close()
        elif _login_browser is not None:
            await _login_browser.close()
    if _login_playwright is not None:
        await _login_playwright.stop()
    _login_playwright = None
    _login_browser = None
    _login_context = None
    _login_page = None
    _login_cdp = False


async def _login_page_or_start() -> object:
    global _login_playwright, _login_browser, _login_context, _login_page, _login_cdp
    if _login_page is not None and not _login_page.is_closed():
        return _login_page

    _login_playwright = await async_playwright().start()
    if CDP_ENDPOINT:
        await _ensure_browser()
        _login_browser = await _login_playwright.chromium.connect_over_cdp(CDP_ENDPOINT)
        if not _login_browser.contexts:
            raise RuntimeError("The normal WSL Chrome has no browser context")
        _login_context = _login_browser.contexts[0]
        _login_page = await _login_context.new_page()
        _login_cdp = True
        return _login_page
    _login_browser = await _login_playwright.chromium.launch(headless=_login_headless())
    _login_context = await _login_browser.new_context()
    _login_page = await _login_context.new_page()
    return _login_page


async def _first_visible(locator):
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            return candidate
    return None


async def _email_input(page):
    candidates = [
        page.get_by_role("textbox", name=re.compile("e-mail|email", re.IGNORECASE)),
        page.locator("input[type='email']"),
        page.locator("input[name*='email' i]"),
    ]
    for candidate in candidates:
        found = await _first_visible(candidate)
        if found is not None:
            return found
    return None


async def _code_inputs(page):
    selectors = [
        "input[autocomplete='one-time-code']",
        "input[inputmode='numeric']",
        "input[name*='otp' i]",
        "input[name*='code' i]",
        "input[placeholder*='código' i]",
        "input[placeholder*='codigo' i]",
    ]
    for selector in selectors:
        visible = []
        locator = page.locator(selector)
        for index in range(await locator.count()):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                visible.append(candidate)
        if visible:
            return visible
    return []


async def _continue_button(page):
    locator = page.get_by_role(
        "button",
        name=re.compile("continuar|entrar|confirmar|validar|avançar|avancar", re.IGNORECASE),
    )
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible() and await candidate.is_enabled():
            return candidate
    return None


async def _dismiss_cookie_banner(page) -> None:
    locator = page.get_by_role("button", name=re.compile("aceitar|accept", re.IGNORECASE))
    button = await _first_visible(locator)
    if button is not None:
        await button.click()
        await page.wait_for_timeout(250)


async def _security_challenge_visible(page) -> bool:
    try:
        title = await page.title()
        body = await page.locator("body").inner_text(timeout=1500)
        text = f"{title}\n{body}".lower()
        return "cloudflare" in text or "attention required" in text
    except Exception:
        return False


def _is_olx_login_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("conta.olx.com.br") and parsed.path.startswith("/acesso")


async def _login_prompt_visible(page) -> bool:
    if _is_olx_login_url(page.url):
        return True
    return await _email_input(page) is not None


def _probe_payload(
    session_state: str,
    *,
    authenticated: bool = False,
    error_code: str | None = None,
    error_message: str = "",
) -> dict:
    return {
        "session_state": session_state,
        "authenticated": authenticated,
        "checked_at": _utc_now(),
        "error_code": error_code,
        "error_message": error_message,
    }


async def _probe_olx_session(force: bool = False) -> dict:
    """Confirm the OLX account page in a temporary, read-only browser tab.

    Stored cookies are deliberately not sufficient evidence of an authenticated
    OLX session. The result is cached briefly because the settings UI polls the
    status endpoint and repeated account-page navigations look bot-like.
    """
    global _session_probe_cache, _session_probe_cached_at

    if not _session_data_available():
        return _probe_payload("disconnected")

    now = asyncio.get_running_loop().time()
    if (
        not force
        and _session_probe_cache is not None
        and now - _session_probe_cached_at < SESSION_PROBE_CACHE_SECONDS
    ):
        cached = dict(_session_probe_cache)
        cached["probe_cache_age_seconds"] = round(now - _session_probe_cached_at, 1)
        return cached

    # Status polling must be a local/cache-only operation. The API performs
    # one explicit force probe immediately before enqueueing a live run.
    if not force:
        metadata = _read_session_metadata()
        state = str(
            metadata.get("last_probe_state")
            or metadata.get("last_session_state")
            or "disconnected"
        )
        error_code = metadata.get("last_error_code")
        error_message = str(metadata.get("last_error_message") or "")
        if _is_browser_unavailable_code(error_code):
            state = "unavailable"
        probe = _probe_payload(
            state,
            authenticated=state == "connected" and bool(metadata.get("last_verified_at")),
            error_code=error_code,
            error_message=error_message,
        )
        probe["probe_cache_age_seconds"] = None
        return probe

    async with _session_probe_lock:
        now = asyncio.get_running_loop().time()
        if (
            not force
            and _session_probe_cache is not None
            and now - _session_probe_cached_at < SESSION_PROBE_CACHE_SECONDS
        ):
            cached = dict(_session_probe_cache)
            cached["probe_cache_age_seconds"] = round(now - _session_probe_cached_at, 1)
            return cached

        page = None
        browser = None
        context = None
        shared_browser = False
        try:
            async with async_playwright() as playwright:
                browser, context, shared_browser = await _new_context(playwright)
                try:
                    page = await _new_worker_page(context)
                    await _olx_guard.navigate(page, OLX_ACCOUNT_URL, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(750)

                    if await _security_challenge_visible(page):
                        guard_state = await _olx_guard.cooldown()
                        probe = _probe_payload(
                            "error",
                            error_code="olx_access_blocked",
                            error_message="A OLX apresentou uma verificação de segurança ao validar a sessão.",
                        )
                        probe.update({
                            "retry_after_seconds": guard_state["retry_after_seconds"],
                            "reset_at": guard_state["reset_at"],
                            "remaining": guard_state["remaining"],
                        })
                    elif await _login_prompt_visible(page):
                        probe = _probe_payload(
                            "expired",
                            error_code="olx_session_expired",
                            error_message="A OLX solicitou login novamente.",
                        )
                    else:
                        parsed = urlparse(page.url)
                        if parsed.netloc.endswith("conta.olx.com.br"):
                            probe = _probe_payload("connected", authenticated=True)
                        else:
                            probe = _probe_payload(
                                "error",
                                error_code="olx_session_unconfirmed",
                                error_message="A OLX não confirmou a página da conta. Faça login novamente.",
                            )
                finally:
                    await _close_worker_page(page)
                    if not shared_browser:
                        if context is not None:
                            await context.close()
                        if browser is not None:
                            await browser.close()
        except OlxGuardError as exc:
            probe = _probe_payload(
                "error", error_code=exc.code, error_message=exc.detail["message"],
            )
            probe.update(exc.detail)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = detail.get("code", "olx_session_probe_failed")
            message = detail.get("message", "Não foi possível validar a sessão OLX.")
            probe = _probe_payload(
                "unavailable" if str(code).startswith("olx_browser") else "error",
                error_code=code,
                error_message=message,
            )
        except Exception:
            logger.exception("OLX session probe failed")
            probe = _probe_payload(
                "unavailable",
                error_code="olx_session_probe_failed",
                error_message="O navegador da OLX ficou indisponível durante a validação. Tente novamente.",
            )
        _session_probe_cache = dict(probe)
        _session_probe_cached_at = asyncio.get_running_loop().time()
        _update_metadata_from_probe(probe)
        if probe["authenticated"]:
            _set_error(None)
        elif probe["error_code"]:
            _set_error(probe["error_code"], probe["error_message"])
        probe["probe_cache_age_seconds"] = 0.0
        return probe


async def _require_confirmed_session() -> None:
    probe = await _probe_olx_session()
    if probe.get("authenticated"):
        return
    state = probe.get("session_state")
    if state == "unavailable":
        raise HTTPException(status_code=503, detail=_detail(
            probe.get("error_code", "olx_session_unavailable"),
            probe.get("error_message", "O navegador da OLX está indisponível."),
        ))
    raise HTTPException(status_code=401, detail=_detail(
        probe.get("error_code", "olx_session_required"),
        probe.get("error_message", "Faça login na OLX antes de iniciar o pipeline."),
    ))


async def _select_email_method(page) -> bool:
    candidates = [
        page.get_by_text("E-mail", exact=True),
        page.get_by_text(re.compile("E-mail.*código", re.IGNORECASE)),
    ]
    for candidate in candidates:
        option = await _first_visible(candidate)
        if option is not None:
            await option.click()
            return True
    return False


async def _persist_login_state() -> None:
    if _login_context is None:
        raise RuntimeError("Login browser context is not available")
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    await _login_context.storage_state(path=str(SESSION_FILE))
    os.chmod(SESSION_FILE, 0o600)


async def _account_email_from_login_page() -> str:
    """Best-effort identity extraction after a user completes browser login.

    The manual workflow never asks for an email in the app. OLX does not always
    expose it on the account page, so an empty value is preferable to showing a
    guessed or unrelated address.
    """
    if _login_page is None or _login_page.is_closed():
        return ""
    try:
        body = await _login_page.locator("body").inner_text(timeout=1500)
        candidates = re.findall(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])", body)
        return next((candidate.lower() for candidate in candidates if not candidate.lower().endswith("@olx.com.br")), "")
    except Exception:
        return ""


def _persist_confirmed_account(email: str, verified_at: str) -> None:
    _write_session_metadata(
        {
            "schema_version": 1,
            "marketplace": "olx",
            "email": email,
            "authenticated_at": verified_at,
            "last_verified_at": verified_at,
            "last_checked_at": verified_at,
            "last_session_state": "connected",
            "last_probe_state": "connected",
            "last_error_code": None,
            "last_error_message": "",
        }
    )


def _remove_persisted_session() -> None:
    SESSION_FILE.unlink(missing_ok=True)
    SESSION_METADATA_FILE.unlink(missing_ok=True)
    _invalidate_session_probe()


async def _clear_browser_profile_session() -> None:
    """Clear the dedicated OLX Chrome profile when a user logs out or switches.

    The profile is created exclusively for GridScout by the launcher, so
    clearing its cookies does not affect the user's normal browser profile.
    """
    if not CDP_ENDPOINT:
        return
    page = None
    try:
        await _ensure_browser()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(CDP_ENDPOINT)
            if not browser.contexts:
                raise RuntimeError("The normal WSL Chrome has no browser context")
            context = browser.contexts[0]
            await context.clear_cookies()
            page = await context.new_page()
            for origin in (OLX_BASE_URL, "https://conta.olx.com.br"):
                await page.goto(origin, wait_until="domcontentloaded", timeout=30000)
                await page.evaluate("localStorage.clear(); sessionStorage.clear()")
            await page.close()
            page = None
    except BrowserWorkerError:
        raise
    except Exception as exc:
        logger.exception("Could not clear the dedicated OLX browser profile")
        raise BrowserWorkerError(
            "olx_session_clear_failed",
            "Não foi possível limpar a sessão no Chrome visível da OLX.",
        ) from exc
    finally:
        if page is not None and not page.is_closed():
            try:
                await page.close()
            except Exception:
                logger.debug("OLX cleanup page was already closed", exc_info=True)


async def _session_status_payload(force: bool = False) -> dict:
    browser_ready = await _cdp_ready() if CDP_ENDPOINT else True
    cookies = _cached_cookie_count()
    metadata = _read_session_metadata()

    if _login_stage == "starting":
        probe = _probe_payload("login_in_progress")
    elif _login_stage == "manual_login":
        probe = _probe_payload("manual_login")
    elif _login_stage == "code_required":
        probe = _probe_payload("code_required")
    elif _login_stage == "manual_action_required":
        probe = _probe_payload("action_required")
    elif _login_stage == "error":
        probe = _probe_payload(
            "error",
            error_code=_last_error_code,
            error_message=_last_error_message or _login_message,
        )
    else:
        probe = await _probe_olx_session(force=force)
        if CDP_ENDPOINT:
            browser_ready = await _cdp_ready()
        if CDP_ENDPOINT and not browser_ready and _session_data_available():
            probe = _probe_payload(
                "unavailable",
                error_code="olx_browser_unavailable",
                error_message="O Chrome visível da OLX está indisponível. Tente novamente.",
            )

    metadata = _read_session_metadata()
    email = str(metadata.get("email") or "")
    authenticated = bool(probe.get("authenticated")) and not _login_in_progress()
    worker_available = (browser_ready if _session_data_available() else True) if CDP_ENDPOINT else True
    account = {
        "marketplace": "olx",
        "email": email,
        "active": True,
        "authenticated": authenticated,
        "authenticated_at": metadata.get("authenticated_at"),
        "last_verified_at": metadata.get("last_verified_at"),
    }
    return {
        "authenticated": authenticated,
        "session_state": probe.get("session_state", "disconnected"),
        "email": email,
        "accounts": [account] if email else [],
        "marketplace": "olx",
        "authenticated_at": metadata.get("authenticated_at"),
        "last_verified_at": metadata.get("last_verified_at"),
        "last_checked_at": probe.get("checked_at") or metadata.get("last_checked_at"),
        "expires_at": None,
        "cookies_cached": cookies,
        "probe_cache_age_seconds": probe.get("probe_cache_age_seconds"),
        "worker_available": worker_available,
        "browser_ready": browser_ready,
        "read_only": True,
        "login_stage": _login_stage,
        "login_message": _login_message,
        "error_code": probe.get("error_code") or _last_error_code,
        "error_message": probe.get("error_message") or _last_error_message,
        "retry_after_seconds": probe.get("retry_after_seconds"),
        "reset_at": probe.get("reset_at"),
        "remaining": probe.get("remaining"),
    }


@app.get("/health")
async def health():
    browser_ready = await _cdp_ready() if CDP_ENDPOINT else True
    return {
        "status": "ok" if browser_ready else "degraded",
        "parser_version": OLX_PARSER_VERSION,
        "session_data_available": _session_data_available(),
        "browser_ready": browser_ready,
        "read_only": True,
    }


@app.get("/session/status")
async def session_status(force: bool = False):
    return await _session_status_payload(force=force)


@app.get("/rate-limit")
async def rate_limit_status():
    return await _olx_guard.status()


class RateLimitPreflightRequest(BaseModel):
    needed: int


@app.post("/rate-limit/preflight")
async def rate_limit_preflight(request: RateLimitPreflightRequest):
    if request.needed < 1:
        raise HTTPException(status_code=422, detail="needed must be at least 1")
    try:
        return await _olx_guard.preflight(request.needed)
    except OlxGuardError as exc:
        raise _guard_http_exception(exc) from exc


@app.post("/auth/manual-start")
async def manual_auth_start():
    """Open the OLX login page without filling or submitting any credentials."""
    global _login_stage, _login_message, _pending_login_email
    async with _login_lock:
        await _close_login_browser()
        _pending_login_email = ""
        _invalidate_session_probe()
        _login_stage = "manual_login"
        _login_message = "Faça login normalmente no Chrome visível e depois confirme nesta tela."
        _set_error(None)
        try:
            page = await _login_page_or_start()
            await page.goto("https://conta.olx.com.br/acesso", wait_until="domcontentloaded", timeout=60000)
            return {
                "status": _login_stage,
                "message": _login_message,
                "read_only": True,
                "error_code": None,
            }
        except BrowserWorkerError as exc:
            await _close_login_browser()
            _login_stage = "error"
            _login_message = exc.message
            _set_error(exc.code, exc.message)
            raise _http_exception(exc) from exc
        except Exception as exc:
            logger.exception("Could not open manual OLX login")
            await _close_login_browser()
            _login_stage = "error"
            _login_message = "Não foi possível abrir o login manual da OLX. Tente novamente."
            _set_error("olx_manual_login_start_failed", _login_message)
            raise HTTPException(
                status_code=502,
                detail=_detail("olx_manual_login_start_failed", _login_message),
            ) from exc


@app.post("/auth/manual-complete")
async def manual_auth_complete():
    """Persist and validate the session after the user finishes login in Chrome."""
    global _login_stage, _login_message
    if _login_page is None or _login_page.is_closed() or _login_context is None:
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "manual_login_window_closed",
                "A janela de login foi fechada. Abra o login manual novamente.",
            ),
        )

    async with _login_lock:
        try:
            _login_stage = "manual_login"
            _login_message = "Confirmando a sessão diretamente na OLX."
            await _persist_login_state()
            _invalidate_session_probe()
            probe = await _probe_olx_session(force=True)
            if not probe.get("authenticated"):
                _login_message = "A OLX ainda não confirmou o login. Conclua-o no Chrome e tente novamente."
                _set_error("olx_session_not_confirmed", _login_message)
                raise HTTPException(
                    status_code=409,
                    detail=_detail("olx_session_not_confirmed", _login_message),
                )

            email = await _account_email_from_login_page() or str(_read_session_metadata().get("email") or "")
            verified_at = probe.get("checked_at") or _utc_now()
            _persist_confirmed_account(email, verified_at)
            _login_stage = "authenticated"
            _login_message = "Login manual confirmado pela OLX; sessão somente leitura salva."
            _set_error(None)
            await _close_login_browser()
            return {
                "status": _login_stage,
                "message": _login_message,
                "read_only": True,
                "error_code": None,
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Could not confirm manual OLX login")
            _login_stage = "error"
            _login_message = "Não foi possível confirmar o login manual. Tente novamente."
            _set_error("olx_manual_login_confirm_failed", _login_message)
            raise HTTPException(
                status_code=502,
                detail=_detail("olx_manual_login_confirm_failed", _login_message),
            ) from exc


@app.post("/auth/start")
async def auth_start(request: LoginStartRequest):
    global _login_stage, _login_message, _pending_login_email
    email = request.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_email", "Informe um email válido da conta OLX."),
        )

    async with _login_lock:
        await _close_login_browser()
        _pending_login_email = email
        _invalidate_session_probe()
        _login_stage = "starting"
        _login_message = "Abrindo o navegador visível do WSL."
        _set_error(None)
        try:
            page = await _login_page_or_start()
            await page.goto("https://conta.olx.com.br/acesso", wait_until="domcontentloaded", timeout=60000)
            await _dismiss_cookie_banner(page)
            if await _security_challenge_visible(page):
                _login_stage = "manual_action_required"
                _login_message = "A OLX apresentou uma verificação de segurança no navegador visível."
                return {
                    "status": _login_stage,
                    "message": _login_message,
                    "read_only": True,
                    "error_code": "olx_security_challenge",
                }
            email_field = await _email_input(page)
            if email_field is None:
                raise BrowserWorkerError(
                    "olx_login_page_changed",
                    "A página de login da OLX mudou ou não carregou corretamente.",
                    502,
                )
            await email_field.fill(email)
            continue_button = await _continue_button(page)
            if continue_button is None:
                raise BrowserWorkerError(
                    "olx_login_page_changed",
                    "O botão de continuação da OLX não está disponível.",
                    502,
                )
            await continue_button.click()
            await page.wait_for_timeout(500)
            await _dismiss_cookie_banner(page)
            await _select_email_method(page)
            code_fields = []
            for _ in range(20):
                await page.wait_for_timeout(500)
                code_fields = await _code_inputs(page)
                if code_fields:
                    break
            if code_fields:
                _login_stage = "code_required"
                _login_message = "Código solicitado. Informe-o nesta tela."
            else:
                _login_stage = "manual_action_required"
                _login_message = "A OLX solicitou uma etapa adicional no navegador visível."
            _set_error(None)
            return {
                "status": _login_stage,
                "message": _login_message,
                "read_only": True,
                "error_code": None,
            }
        except BrowserWorkerError as exc:
            await _close_login_browser()
            _pending_login_email = ""
            _login_stage = "error"
            _login_message = exc.message
            _set_error(exc.code, exc.message)
            raise _http_exception(exc) from exc
        except Exception as exc:
            logger.exception("OLX login start failed")
            await _close_login_browser()
            _pending_login_email = ""
            _login_stage = "error"
            _login_message = "Não foi possível iniciar o login. Tente novamente."
            _set_error("olx_login_start_failed", _login_message)
            raise HTTPException(
                status_code=502,
                detail=_detail("olx_login_start_failed", _login_message),
            ) from exc


@app.post("/auth/verify-code")
async def auth_verify_code(request: LoginCodeRequest):
    global _login_stage, _login_message, _pending_login_email
    code = re.sub(r"\D", "", request.code)
    if not 4 <= len(code) <= 8:
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_login_code", "O código deve conter de 4 a 8 dígitos."),
        )
    if _login_page is None or _login_page.is_closed():
        _login_stage = "error"
        _login_message = "A janela de login foi fechada. Inicie o login novamente."
        _set_error("login_window_closed", _login_message)
        raise HTTPException(
            status_code=409,
            detail=_detail("login_window_closed", _login_message),
        )

    async with _login_lock:
        code_fields = await _code_inputs(_login_page)
        if not code_fields:
            _login_stage = "error"
            _login_message = "A página da OLX não está aguardando um código. Inicie o login novamente."
            _set_error("login_code_unavailable", _login_message)
            raise HTTPException(
                status_code=409,
                detail=_detail("login_code_unavailable", _login_message),
            )
        try:
            if len(code_fields) == 1:
                await code_fields[0].fill(code)
            else:
                for field, digit in zip(code_fields, code):
                    await field.fill(digit)
            continue_button = await _continue_button(_login_page)
            if continue_button is not None:
                await continue_button.click()
            remaining_code_fields = []
            for _ in range(20):
                await _login_page.wait_for_timeout(500)
                remaining_code_fields = await _code_inputs(_login_page)
                if "/acesso" not in _login_page.url and not remaining_code_fields:
                    break
            if "/acesso" not in _login_page.url and not remaining_code_fields:
                await _persist_login_state()
                _invalidate_session_probe()
                probe = await _probe_olx_session(force=True)
                if not probe.get("authenticated"):
                    _remove_persisted_session()
                    _login_stage = "error"
                    _login_message = "A OLX não confirmou a sessão após o código. Faça login novamente."
                    _set_error("olx_session_not_confirmed", _login_message)
                    await _close_login_browser()
                    _pending_login_email = ""
                    raise HTTPException(
                        status_code=409,
                        detail=_detail("olx_session_not_confirmed", _login_message),
                    )

                verified_at = probe.get("checked_at") or _utc_now()
                _persist_confirmed_account(_pending_login_email, verified_at)
                _login_stage = "authenticated"
                _login_message = "Login confirmado pela OLX; sessão somente leitura salva."
                _set_error(None)
                await _close_login_browser()
                _pending_login_email = ""
                return {
                    "status": _login_stage,
                    "message": _login_message,
                    "read_only": True,
                    "error_code": None,
                }

            _login_stage = "code_required"
            _login_message = "O código não concluiu o login; confira-o e tente novamente."
            _set_error("login_code_rejected", _login_message)
            return {"status": _login_stage, "message": _login_message, "read_only": True}
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            logger.exception("OLX login code verification failed")
            _login_stage = "error"
            _login_message = "Não foi possível concluir o login. Confira o código e tente novamente."
            _set_error("olx_login_verify_failed", _login_message)
            raise HTTPException(
                status_code=502,
                detail=_detail("olx_login_verify_failed", _login_message),
            ) from exc


@app.post("/auth/logout")
async def auth_logout():
    global _login_stage, _login_message, _pending_login_email
    async with _login_lock:
        await _close_login_browser()
        try:
            await _clear_browser_profile_session()
            _remove_persisted_session()
        except BrowserWorkerError as exc:
            raise _http_exception(exc) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Could not remove the OLX session") from exc
        _login_stage = "idle"
        _login_message = "Sessão removida."
        _pending_login_email = ""
        _set_error(None)
        return {"status": "logged_out", "message": _login_message}


@app.post("/search")
async def search(request: SearchRequest):
    if request.limit < 1 or request.limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if request.page < 1 or request.page > 1000:
        raise HTTPException(status_code=422, detail="page must be between 1 and 1000")
    await _require_confirmed_session()
    search_url = _search_url(request)
    async with async_playwright() as playwright:
        browser, context, shared_browser = await _new_context(playwright)
        page = await _new_worker_page(context)
        try:
            response = await _olx_guard.navigate(page, search_url, wait_until="domcontentloaded", timeout=OLX_NAV_TIMEOUT_MS)
            if response is not None and response.status >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=_detail("olx_search_http_error", f"A OLX respondeu HTTP {response.status} para a busca."),
                )
            cards_locator = page.locator("a[data-testid='adcard-link']")
            try:
                await cards_locator.first.wait_for(state="visible", timeout=OLX_RESULT_WAIT_MS)
            except Exception:
                await page.wait_for_timeout(750)

            body = await page.locator("body").inner_text()
            if _page_blocked(body):
                guard_state = await _olx_guard.cooldown()
                raise HTTPException(
                    status_code=429,
                    detail={"code": "olx_access_blocked", "message": "A OLX apresentou uma página de bloqueio ou verificação. Nenhuma ação foi tentada para contorná-la.", "retry_after_seconds": guard_state["retry_after_seconds"], "reset_at": guard_state["reset_at"], "remaining": guard_state["remaining"]},
                )

            total_results = _parse_result_count(
                await page.locator("#total-of-ads").inner_text()
                if await page.locator("#total-of-ads").count()
                else body
            )
            cards_count = await cards_locator.count()
            if total_results > 0 and cards_count == 0:
                raise HTTPException(
                    status_code=502,
                    detail=_detail(
                        "olx_parser_mismatch",
                        f"A OLX informou {total_results} resultados, mas nenhum card foi reconhecido pelo parser {OLX_PARSER_VERSION}.",
                    ),
                )

            results = []
            seen = set()
            recommendation_cards_skipped = cards_count if total_results == 0 else 0
            for index in range(cards_count) if total_results > 0 else []:
                link = cards_locator.nth(index)
                href = await link.get_attribute("href")
                if not href:
                    continue
                url = urljoin(OLX_BASE_URL, href)
                if _is_recommendation_url(url):
                    recommendation_cards_skipped += 1
                    continue
                if url in seen:
                    continue
                card = link.locator("xpath=ancestor::*[contains(@class, 'olx-adcard') or @data-testid='adcard' or @data-ds-component='DS-AdCard']").first
                if not await card.count():
                    card = link.locator("xpath=ancestor::section | ancestor::article | ancestor::div[contains(@class, 'card') or contains(@class, 'ad')]").first
                card_text = (await card.inner_text()).strip() if await card.count() else (await link.inner_text()).strip()
                title = _clean_text(await link.get_attribute("title"))
                if not title and await card.locator("h2").count():
                    title = _clean_text(await card.locator("h2").first.inner_text())
                if not title:
                    title = _clean_text((await link.inner_text()).split("\n", 1)[0])
                price_locator = card.locator(".olx-adcard__price, [data-testid*='price'], [class*='adcard__price'], [class*='Price']") if await card.count() else page.locator(".olx-adcard__price, [data-testid*='price'], [class*='adcard__price'], [class*='Price']").nth(index)
                price_text = await price_locator.first.inner_text() if await price_locator.count() else ""
                price, price_origin = _parse_price(price_text)
                if price is None and card_text:
                    fallback_price, fallback_origin = _parse_price(card_text)
                    if fallback_price is not None:
                        price = fallback_price
                        price_origin = fallback_origin
                        price_match = re.search(r"R\$\s*[\d.]+(?:,[\d]{1,2})?", card_text)
                        if price_match:
                            price_text = price_match.group(0)
                if (request.require_price or (request.min_price is not None and request.min_price > 0)) and (price is None or price <= 0):
                    continue
                if request.min_price is not None and price is not None and price < request.min_price:
                    continue
                if request.max_price is not None and price is not None and price > request.max_price:
                    continue
                location_locator = card.locator(".olx-adcard__location, [data-testid*='location'], [class*='adcard__location'], [class*='Location']") if await card.count() else page.locator(".olx-adcard__location, [data-testid*='location'], [class*='adcard__location'], [class*='Location']").nth(index)
                location = _clean_text(await location_locator.first.inner_text()) if await location_locator.count() else ""
                if location:
                    location = re.sub(r'\s*(?:hoje|ontem|\d{1,2}\s+de\s+[a-z]+|\d{1,2}/\d{1,2}).*$', '', location, flags=re.IGNORECASE).strip(' -,\t')
                if not location and card_text:
                    loc_match = re.search(r"([A-Za-zÀ-ÿ\s]{2,30})\s*-\s*([A-Z]{2})\b", card_text)
                    if loc_match:
                        location = _clean_text(loc_match.group(0))
                location_origin = "dom" if location else "missing"
                seen.add(url)
                results.append(
                    {
                        "external_id": _external_id(url),
                        "title": title or "OLX listing",
                        "price": price,
                        "price_origin": price_origin,
                        "price_raw": price_text,
                        "location": location,
                        "location_origin": location_origin,
                        "condition": "",
                        "seller": "",
                        "url": url,
                        "field_coverage": {
                            "title": bool(title),
                            "price": price is not None,
                            "location": bool(location),
                            "url": bool(url),
                        },
                        "parser_version": OLX_PARSER_VERSION,
                    }
                )
                if len(results) >= request.limit:
                    break
            return {
                "items": results,
                "read_only": True,
                "url": search_url,
                "diagnostics": {
                    "parser_version": OLX_PARSER_VERSION,
                    "total_results": total_results,
                    "cards_detected": cards_count,
                    "items_returned": len(results),
                    "recommendation_cards_skipped": recommendation_cards_skipped,
                    "page_state": (
                        "results" if total_results
                        else "recommendations" if cards_count
                        else "empty"
                    ),
                },
            }
        except OlxGuardError as exc:
            raise _guard_http_exception(exc) from exc
        finally:
            await _close_worker_page(page)
            if not shared_browser:
                await context.close()
                await browser.close()


@app.post("/listing")
async def listing_detail(request: DetailRequest):
    if not request.external_id.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="external_id must be a URL returned by search")
    await _require_confirmed_session()
    async with async_playwright() as playwright:
        browser, context, shared_browser = await _new_context(playwright)
        page = await _new_worker_page(context)
        try:
            response = await _olx_guard.navigate(page, request.external_id, wait_until="domcontentloaded", timeout=OLX_NAV_TIMEOUT_MS)
            if response is not None and response.status >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=_detail("olx_listing_http_error", f"A OLX respondeu HTTP {response.status} para o anúncio."),
                )
            await page.wait_for_timeout(750)
            body = await page.locator("body").inner_text()
            if _page_blocked(body):
                guard_state = await _olx_guard.cooldown()
                raise HTTPException(
                    status_code=429,
                    detail={"code": "olx_access_blocked", "message": "A OLX apresentou uma página de bloqueio ou verificação. Nenhuma ação foi tentada para contorná-la.", "retry_after_seconds": guard_state["retry_after_seconds"], "reset_at": guard_state["reset_at"], "remaining": guard_state["remaining"]},
                )

            lines = [line.strip() for line in body.splitlines() if line.strip()]
            json_ld = await page.locator("script[type='application/ld+json']").all_text_contents()
            product = _find_json_ld_product(json_ld)
            title = _clean_text(product.get("name"))
            if not title:
                for selector in ("meta[property='og:title']", "h1", "[data-testid='ad-title']"):
                    if await page.locator(selector).count():
                        title = _clean_text(
                            await page.locator(selector).first.get_attribute("content")
                            if selector.startswith("meta")
                            else await page.locator(selector).first.inner_text()
                        )
                        if title:
                            break
            description = _clean_html(product.get("description"))
            if not description:
                for selector in ("meta[property='og:description']", "[data-testid='ad-description']", "#description"):
                    if await page.locator(selector).count():
                        description = _clean_text(
                            await page.locator(selector).first.get_attribute("content")
                            if selector.startswith("meta")
                            else await page.locator(selector).first.inner_text()
                        )
                        if description:
                            break
            raw_price = _extract_offer_price(product)
            price: float | None = raw_price if raw_price > 0 else None
            price_origin = "structured" if price is not None else "missing"
            if price is None:
                for selector in (
                    "[data-testid='ad-price']",
                    "[data-testid='price']",
                    "[data-testid*='price']",
                    "[class*='adcard__price']",
                    "[class*='ad-price']",
                    "[class*='price-wrapper']",
                    "[class*='Price']",
                    "[class*='price']",
                    "h2",
                    "h3",
                    "[data-ds-component*='Text']",
                ):
                    locator = page.locator(selector)
                    count = min(await locator.count(), 6)
                    for idx in range(count):
                        elem = locator.nth(idx)
                        if await elem.is_visible():
                            parsed_price, p_orig = _parse_price(await elem.inner_text())
                            if parsed_price is not None and parsed_price > 0:
                                price = parsed_price
                                price_origin = p_orig
                                break
                    if price is not None:
                        break

            if price is None and lines:
                for line in lines:
                    parsed_price, p_orig = _parse_price(line)
                    if parsed_price is not None and parsed_price > 0:
                        price = parsed_price
                        price_origin = p_orig
                        break

            city, state = _parse_location(lines)
            condition = _line_value(lines, {"condição", "condicao", "estado"})
            if not condition:
                item_cond = str(product.get("itemCondition") or "").lower()
                if "used" in item_cond:
                    condition = "Usado"
                elif "new" in item_cond:
                    condition = "Novo"
                elif "refurbished" in item_cond:
                    condition = "Recondicionado"

            delivery_evidence = _page_evidence_lines(
                lines,
                ("entrega", "envio", "frete", "retirada", "shipping", "pickup"),
            )
            seller_name = ""
            seller_photo = page.locator("img[alt*='Foto de']")
            if await seller_photo.count():
                alt = _clean_text(await seller_photo.first.get_attribute("alt"))
                seller_name = re.sub(r"^Foto de\s+", "", alt, flags=re.IGNORECASE).strip()
                if seller_name.casefold() in {"perfil", "foto de perfil"}:
                    seller_name = ""
            if not seller_name:
                for index, line in enumerate(lines):
                    if line.casefold().startswith("na olx desde"):
                        candidates = lines[max(0, index - 3):index]
                        candidates = [
                            candidate for candidate in candidates
                            if not candidate.casefold().startswith("último acesso")
                            and not candidate.casefold().startswith("ultimo acesso")
                        ]
                        if candidates:
                            seller_name = candidates[-1]
                            break
            seller_verification = "UNKNOWN"
            seller_evidence = []
            verification_locators = (
                "[data-testid*='verified']",
                "[aria-label*='verificado']",
                "[aria-label*='Verified']",
            )
            for selector in verification_locators:
                verified = page.locator(selector)
                if await verified.count():
                    seller_verification = "VERIFIED"
                    label = _clean_text(await verified.first.inner_text())
                    seller_evidence = [label] if label else ["Selo de verificação visível na página"]
                    break
            if seller_verification == "UNKNOWN":
                seller_evidence = _page_evidence_lines(
                    lines,
                    ("verificado", "avaliação", "avaliacao", "na olx desde", "último acesso", "ultimo acesso"),
                )
            seller_rating = _parse_seller_rating(lines)
            photos = await _extract_page_photos(page)
            attributes = {"read_only": True, "parser_version": OLX_PARSER_VERSION}
            attribute_labels = {
                "categoria", "fabricante", "condição", "condicao", "estado", "marca", "modelo",
                "armazenamento", "memória ram", "memoria ram", "memória de vídeo - vram",
                "memoria de video - vram", "localização", "localizacao", "cor", "versão", "versao",
                "console", "garantia", "acessórios", "acessorios", "tipo",
            }
            normalized_lines = [line.strip() for line in lines]
            for index, line in enumerate(normalized_lines[:-1]):
                key = line.casefold().rstrip(":")
                if key in attribute_labels:
                    value = normalized_lines[index + 1]
                    if value.casefold().rstrip(":") not in attribute_labels:
                        attributes[key] = value

            item_id = str(product.get("identifier") or product.get("sku") or product.get("mpn") or product.get("@id") or "")
            if not item_id or not re.search(r"\d{4,}", item_id):
                for pattern in (r"-(\d{6,15})(?:[/?#]|$)", r"/(\d{6,15})(?:\.htm|[/?#]|$)", r"[?&]id=(\d+)", r"/vi/(\d+)"):
                    match = re.search(pattern, request.external_id)
                    if match:
                        item_id = match.group(1)
                        break
            if not item_id:
                item_id = f"ext-{hashlib.sha1(request.external_id.encode('utf-8')).hexdigest()[:12]}"

            missing_fields = []
            if not title:
                missing_fields.append("title")
            if price is None or price <= 0:
                missing_fields.append("price")

            if missing_fields:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "page_missing_required_fields",
                        "kind": "parser",
                        "stage": "detail",
                        "retry_policy": "skipped_unverified",
                        "missing_fields": missing_fields,
                        "parser_version": OLX_PARSER_VERSION,
                        "message": f"O anúncio carregou, mas campos obrigatórios não foram extraídos com segurança ({', '.join(missing_fields)}).",
                        "diagnostics": {
                            "page_state": "detail_incomplete",
                            "selectors_tried": 5,
                            "provenance": {
                                "json_ld_present": bool(product),
                                "title_found": bool(title),
                                "price_found": price is not None and price > 0,
                                "item_id_found": bool(item_id),
                            },
                            "sanitized_snippet": _sanitize_snippet(body[:2000]),
                        },
                    },
                )
            return {
                "external_id": request.external_id,
                "title": title,
                "description": description,
                "price": price,
                "location_state": state,
                "location_city": city,
                "seller_name": seller_name,
                "seller_rating": seller_rating,
                "condition": condition,
                "attributes": attributes,
                "source_url": request.external_id,
                "marketplace_item_id": item_id,
                "delivery_text": " | ".join(delivery_evidence),
                "seller_verification": seller_verification,
                "seller_evidence": seller_evidence,
                "photos": photos,
                "external_navigation_count": 0,
                "parser_version": OLX_PARSER_VERSION,
            }

        except OlxGuardError as exc:
            raise _guard_http_exception(exc) from exc
        finally:
            await _close_worker_page(page)
            if not shared_browser:
                await context.close()
                await browser.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("BROWSER_WORKER_PORT", "8100")))
