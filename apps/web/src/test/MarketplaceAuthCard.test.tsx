import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { MarketplaceAuthCard } from '../components/MarketplaceAuthCard';
import * as api from '../api';

vi.mock('../api', () => ({ fetcher: vi.fn() }));

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MarketplaceAuthCard />
    </QueryClientProvider>,
  );
}

const confirmedSession = {
  authenticated: true,
  session_state: 'connected',
  email: 'buyer@example.com',
  accounts: [{
    marketplace: 'olx',
    email: 'buyer@example.com',
    active: true,
    authenticated: true,
    authenticated_at: '2026-08-24T15:00:00Z',
    last_verified_at: '2026-08-24T16:00:00Z',
  }],
  marketplace: 'olx',
  authenticated_at: '2026-08-24T15:00:00Z',
  last_verified_at: '2026-08-24T16:00:00Z',
  last_checked_at: '2026-08-24T16:00:00Z',
  expires_at: null,
  cookies_cached: 24,
  manual_browser_session: true,
  human_emulation_active: false,
  read_only_scope: ['search', 'listing_detail'],
  write_actions: false,
  login_stage: 'authenticated',
  login_message: 'Login confirmado pela OLX.',
  worker_available: true,
  browser_ready: true,
  error_code: null,
  error_message: '',
};

describe('MarketplaceAuthCard', () => {
  it('shows the full connected email and keeps diagnostics collapsed', async () => {
    vi.mocked(api.fetcher).mockResolvedValue(confirmedSession);
    renderCard();

    await waitFor(() => expect(screen.getByText('Conta conectada')).toBeInTheDocument());
    expect(screen.getAllByText('buyer@example.com').length).toBeGreaterThan(0);
    expect(screen.getByText('Detalhes da sessão')).toBeInTheDocument();
    expect(screen.getByText('Detalhes da sessão').closest('details')).not.toHaveAttribute('open');

    fireEvent.click(screen.getByText('Detalhes da sessão'));
    expect(screen.getByText('Detalhes da sessão').closest('details')).toHaveAttribute('open');
    expect(screen.getByText('Cookies em cache')).toBeInTheDocument();
    expect(screen.getByText('Nenhuma ação de escrita habilitada')).toBeInTheDocument();
  });

  it('offers manual browser login when the OLX session expires', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      ...confirmedSession,
      authenticated: false,
      session_state: 'expired',
      login_stage: 'idle',
      error_code: 'olx_session_expired',
      error_message: 'A OLX solicitou login novamente.',
    });
    renderCard();

    await waitFor(() => expect(screen.getByText('Sessão expirada')).toBeInTheDocument());
    expect(screen.getAllByText('buyer@example.com').length).toBeGreaterThan(0);
    expect(screen.getByText('Abrir login manual no navegador')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('does not request a new login when only the visible browser is unavailable', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      ...confirmedSession,
      authenticated: false,
      session_state: 'unavailable',
      worker_available: false,
      browser_ready: false,
      error_code: 'olx_browser_unavailable',
      error_message: 'O Chrome visível da OLX está indisponível.',
    });
    renderCard();

    await waitFor(() => expect(screen.getByText('Serviço indisponível')).toBeInTheDocument());
    expect(screen.getByText('Verificar navegador novamente')).toBeInTheDocument();
    expect(screen.queryByText('Abrir login manual no navegador')).not.toBeInTheDocument();
  });

  it('offers manual browser login when disconnected and visible browser is idle', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      ...confirmedSession,
      authenticated: false,
      session_state: 'disconnected',
      email: '',
      accounts: [],
      cookies_cached: 0,
      login_stage: 'idle',
      login_message: '',
      worker_available: true,
      browser_ready: false,
      error_code: null,
      error_message: '',
    });
    renderCard();

    await waitFor(() => expect(screen.getByText('Não conectada')).toBeInTheDocument());
    expect(screen.getByText('Nenhuma conta OLX registrada')).toBeInTheDocument();
    expect(screen.getByText('Abrir login manual no navegador')).toBeInTheDocument();
  });
});
