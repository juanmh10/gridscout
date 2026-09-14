#!/usr/bin/env python3
"""
GridScout Live Log Monitor
Streams and abstracts system, pipeline, API, and database logs into clean,
objective real-time operational events without noise.
"""

import datetime
import os
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Set

# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"


def timestamp() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def print_banner() -> None:
    print(f"\n{CYAN}{BOLD}{'='*78}{RESET}")
    print(f"{CYAN}{BOLD}  🔭 GRIDSCOUT · MONITOR DE EXECUÇÃO EM TEMPO REAL{RESET}")
    print(f"{CYAN}{BOLD}{'='*78}{RESET}")
    print(f"  {BOLD}Web UI:{RESET}     {BLUE}http://localhost:3000{RESET}")
    print(f"  {BOLD}API:{RESET}        {BLUE}http://localhost:8000{RESET}")
    print(f"  {BOLD}PostgreSQL:{RESET} {GREEN}localhost:5432 (marketradar){RESET}")
    print(f"{CYAN}{'-'*78}{RESET}")
    print(f"  {DIM}Filtrando ruídos de polling e formatando eventos de pipelines e IA...{RESET}")
    print(f"{CYAN}{'='*78}{RESET}\n")


# Noise patterns to suppress
NOISE_PATTERNS = [
    r'GET /api/v1/health HTTP/1\.1"\s+200',
    r'GET /health HTTP/1\.1"\s+200',
    r'GET /api/v1/status HTTP/1\.1"\s+200',
    r'GET /api/v1/marketplace/auth/status HTTP/1\.1"\s+200',
    r'GET /api/v1/marketplace/rate-limit HTTP/1\.1"\s+200',
    r'GET /api/v1/marketplace/rate-limit\?marketplace=olx HTTP/1\.1"\s+200',
    r'GET /api/v1/pipelines\?page=\d+&page_size=\d+ HTTP/1\.1"\s+200',
    r'GET /api/v1/search-scopes\?marketplace=\w+&enabled_only=true HTTP/1\.1"\s+200',
    r'GET /api/v1/search-definitions HTTP/1\.1"\s+200',
    r'GET /assets/.*?\.(js|css|svg|ico|png|woff2?) HTTP/1\.1"\s+(200|304)',
    r'pg_isready',
    r'database system was shut down',
    r'database system is ready to accept connections',
    r'checkpoint starting',
    r'checkpoint complete',
    r'reconcile-pipelines',
    r'Running upgrade head',
    r'Context impl PostgresqlImpl',
    r'Will assume transactional DDL',
    r'Started server process',
    r'Waiting for application startup',
    r'Application startup complete',
    r'Uvicorn running on',
    r'Shutting down',
    r'Waiting for application shutdown',
    r'Application shutdown complete',
    r'Finished server process',
    r'POST /auth/start HTTP/1\.1"\s+200',
]

NOISE_REGEX = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)


def should_ignore_line(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return True
    if NOISE_REGEX.search(clean):
        return True
    return False


def format_abstracted_log(raw_line: str) -> Optional[str]:
    """Parse container log lines and produce a concise, human-friendly operational log."""
    line = raw_line.strip()
    if should_ignore_line(line):
        return None

    ts = timestamp()

    # 1. API Requests
    if ' - "POST /api/v1/pipelines/run' in line:
        return f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[API/PIPELINE]{RESET} ⚡ Nova requisição de pipeline recebida -> {GREEN}202 Aceito{RESET}"
    if ' - "POST /api/v1/search-definitions' in line:
        return f"{DIM}[{ts}]{RESET} {CYAN}{BOLD}[API/BUSCA]{RESET} 💾 Novo escopo de busca salvo -> {GREEN}201 Criado{RESET}"
    if ' - "POST /api/v1/pipelines/high-volume/preflight' in line:
        return f"{DIM}[{ts}]{RESET} {CYAN}{BOLD}[PREFLIGHT]{RESET} 📊 Verificação prévia de capacidade para Alto Volume"
    if ' - "POST /api/v1/opportunities/feedback' in line:
        return f"{DIM}[{ts}]{RESET} {MAGENTA}{BOLD}[FEEDBACK]{RESET} 📝 Feedback de oportunidade registrado pelo operador"

    # API Errors (4xx, 5xx)
    http_error_match = re.search(r' - "(GET|POST|PUT|DELETE)\s+([^"]+)\s+HTTP/[^"]+"\s+([45]\d\d)', line)
    if http_error_match:
        method, path, code = http_error_match.groups()
        return f"{DIM}[{ts}]{RESET} {RED}{BOLD}[API/ERRO]{RESET} ❌ {method} {path} -> {RED}{BOLD}{code}{RESET}"

    # 2. Worker & Pipeline Lifecycle Events
    if "Worker started" in line:
        return f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[WORKER]{RESET} ⚙️  Worker ativo e monitorando fila de pipelines"

    if "Iniciando execução" in line or ("Pipeline" in line and "iniciado" in line):
        return f"{DIM}[{ts}]{RESET} {BLUE}{BOLD}[PIPELINE]{RESET} 🚀 {line.split('|')[-1].strip()}"

    if "Busca marketplace:" in line:
        match = re.search(r"Busca marketplace:\s*'([^']+)'(?:\s*\(limite=(\d+)\))?", line)
        if match:
            q, lim = match.groups()
            return f"{DIM}[{ts}]{RESET} {CYAN}{BOLD}[BUSCA]{RESET} 🔎 Consultando marketplace: {BOLD}'{q}'{RESET} (limite: {lim or 'N/A'})"

    if "Capturados" in line and "anúncios na busca" in line:
        match = re.search(r"Capturados\s+(\d+)\s+anúncios na busca\s*'([^']*)'", line)
        if match:
            count, q = match.groups()
            return f"{DIM}[{ts}]{RESET} {CYAN}{BOLD}[CAPTURA]{RESET} 📥 {BOLD}{count} anúncios{RESET} coletados para '{q}'"

    if "Anúncio" in line and "->" in line:
        match = re.search(r"Anúncio\s+'([^']+)'\s+\(R\$\s*([^)]+)\)\s*->\s*(.*)", line)
        if match:
            title, price, status = match.groups()
            status_color = GREEN if status.lower() == "confirmed" else (YELLOW if status.lower() == "unverified" else RED)
            return f"{DIM}[{ts}]{RESET} {BLUE}{BOLD}[ANÚNCIO]{RESET} 📦 '{title[:45]}' | R$ {price} -> {status_color}{status}{RESET}"

    if "Oportunidade encontrada:" in line:
        match = re.search(r"Oportunidade encontrada:\s*'([^']+)'\s+por R\$\s*([^\s]+)", line)
        if match:
            title, price = match.groups()
            return f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[OPORTUNIDADE]{RESET} 💰 {BOLD}'{title[:50]}'{RESET} por {GREEN}R$ {price}{RESET}"

    if "Pipeline" in line and "concluído:" in line:
        match = re.search(r"Pipeline\s+(\S+)\s+concluído:\s*(\d+)\s+oportunidades", line)
        if match:
            pid, opps = match.groups()
            return f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[PIPELINE]{RESET} ✅ Pipeline finalizado com sucesso! {BOLD}{opps} oportunidade(s){RESET} gerada(s)"

    # High Volume Logs
    if "[HIGH-VOLUME" in line or "Fase:" in line:
        return f"{DIM}[{ts}]{RESET} {MAGENTA}{BOLD}[ALTO VOLUME]{RESET} 📈 {line.split('|')[-1].strip()}"

    # Browser Worker / OLX Logs
    if "OLX session" in line or "Visible OLX Chrome" in line or "CDP" in line:
        if "did not expose CDP" in line or "Could not connect" in line:
            return f"{DIM}[{ts}]{RESET} {RED}{BOLD}[OLX/BROWSER]{RESET} ⚠️ Navegador OLX desconectado ou aguardando inicialização"
        if "connected" in line or "ready" in line:
            return f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[OLX/BROWSER]{RESET} 🌐 Navegador OLX conectado com sucesso via CDP"

    if "Rate limit" in line or "cooldown" in line.lower() or "olx_budget_exhausted" in line:
        return f"{DIM}[{ts}]{RESET} {YELLOW}{BOLD}[OLX/COTA]{RESET} ⏸️ Limite de navegação atingido. Aguardando tempo de espera (cooldown)..."

    # Database / Migrations
    if "alembic" in line.lower() and "upgrade" in line.lower():
        return f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[BANCO]{RESET} 🗃️ Migrações de banco de dados aplicadas"

    # General Errors and Warnings
    if "ERROR" in line or "error" in line.lower() or "exception" in line.lower():
        clean_msg = line.split("|")[-1].strip()
        if len(clean_msg) > 120:
            clean_msg = clean_msg[:120] + "..."
        return f"{DIM}[{ts}]{RESET} {RED}{BOLD}[ALERTA/ERRO]{RESET} ❌ {clean_msg}"

    if "WARNING" in line or "warning" in line.lower():
        clean_msg = line.split("|")[-1].strip()
        return f"{DIM}[{ts}]{RESET} {YELLOW}{BOLD}[AVISO]{RESET} ⚠️ {clean_msg}"

    # Fallback: general informative line with container name prefix
    prefix = ""
    if "api-1" in line:
        prefix = f"{CYAN}[API]{RESET} "
    elif "worker-1" in line:
        prefix = f"{BLUE}[WORKER]{RESET} "
    elif "browser-worker-1" in line:
        prefix = f"{MAGENTA}[BROWSER]{RESET} "

    clean_content = line.split("|")[-1].strip()
    if clean_content and len(clean_content) < 140:
        return f"{DIM}[{ts}]{RESET} {prefix}{clean_content}"

    return None


def run_database_event_watcher(stop_event: threading.Event) -> None:
    """Watch PostgreSQL for live pipeline transitions and opportunity creations."""
    db_url = os.getenv("DATABASE_URL", "postgresql://marketradar:marketradar@localhost:5432/marketradar")
    try:
        import psycopg
    except ImportError:
        return

    last_transition_id = 0
    last_opp_id = 0
    seen_events: Set[str] = set()

    while not stop_event.is_set():
        try:
            with psycopg.connect(db_url, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    # 1. Fetch recent pipeline state transitions
                    cur.execute(
                        """
                        SELECT id, pipeline_run_id, from_status, to_status, reason_code, message, occurred_at
                        FROM pipeline_state_transitions
                        ORDER BY id DESC LIMIT 10
                        """
                    )
                    rows = cur.fetchall()
                    for row in reversed(rows):
                        t_id, run_id, from_st, to_st, reason, msg, occ_at = row
                        ev_key = f"trans_{t_id}"
                        if ev_key not in seen_events:
                            seen_events.add(ev_key)
                            if last_transition_id > 0:  # Only print new events during stream
                                ts = timestamp()
                                st_color = GREEN if to_st == "completed" else (BLUE if to_st in ("running", "claimed") else YELLOW)
                                print(
                                    f"{DIM}[{ts}]{RESET} {BLUE}{BOLD}[PIPELINE STATUS]{RESET} 🔄 Pipeline alterou status: {DIM}{from_st}{RESET} -> {st_color}{BOLD}{to_st}{RESET} ({msg or reason})"
                                )
                            last_transition_id = max(last_transition_id, t_id)

                    # 2. Fetch recent opportunities
                    cur.execute(
                        """
                        SELECT id, pipeline_run_id, title, price, market_median, deal_score, created_at
                        FROM opportunities
                        ORDER BY id DESC LIMIT 5
                        """
                    )
                    opp_rows = cur.fetchall()
                    for row in reversed(opp_rows):
                        o_id, r_id, title, price, median, score, c_at = row
                        ev_key = f"opp_{o_id}"
                        if ev_key not in seen_events:
                            seen_events.add(ev_key)
                            if last_opp_id > 0:
                                ts = timestamp()
                                print(
                                    f"{DIM}[{ts}]{RESET} {GREEN}{BOLD}[OPORTUNIDADE]{RESET} 💰 {BOLD}'{title[:50]}'{RESET} | Preço: {GREEN}R$ {price:,.2f}{RESET} (Mediana: R$ {median:,.2f}, Score: {score})"
                                )
                            last_opp_id = max(last_opp_id, o_id)

        except Exception:
            pass  # Reconnect on next loop

        time.sleep(1.0)


def stream_docker_logs() -> None:
    """Stream docker compose logs and output formatted, abstracted events."""
    print_banner()

    stop_event = threading.Event()
    db_thread = threading.Thread(target=run_database_event_watcher, args=(stop_event,), daemon=True)
    db_thread.start()

    cmd = ["docker", "compose", "logs", "-f", "--tail=20", "api", "worker", "browser-worker"]

    def sigint_handler(sig, frame):
        stop_event.set()
        print(f"\n{YELLOW}Encerrando monitor de logs do GridScout.{RESET}\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, sigint_handler)

    while True:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if proc.stdout is None:
                time.sleep(2)
                continue

            for raw_line in iter(proc.stdout.readline, ""):
                if not raw_line:
                    break
                formatted = format_abstracted_log(raw_line)
                if formatted:
                    print(formatted, flush=True)

            proc.stdout.close()
            proc.wait()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"{DIM}[{timestamp()}]{RESET} {YELLOW}Aguardando containers do Docker... ({exc}){RESET}")
            time.sleep(3)


if __name__ == "__main__":
    stream_docker_logs()
