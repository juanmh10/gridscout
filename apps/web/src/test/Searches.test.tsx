import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Searches from '../pages/Searches';
import * as api from '../api';

vi.mock('../api', async () => {
  const original = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...original,
    listSearchDefinitions: vi.fn(),
    listSearchSessions: vi.fn(),
    createSearchDefinition: vi.fn(),
    updateSearchDefinition: vi.fn(),
    deleteSearchDefinition: vi.fn(),
    createSearchSession: vi.fn(),
    saveSearchDraft: vi.fn(),
    confirmSearchSession: vi.fn(),
    sendSearchMessage: vi.fn(),
  };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><Searches /></MemoryRouter></QueryClientProvider>);
}

describe('Buscas', () => {
  it('shows the human-readable scope and prepares it for Pipelines instead of executing it', async () => {
    const plan = {
      schema_version: 'search-plan-v1',
      intent: { category: 'notebook', raw_query: 'notebook IPS wifi 5ghz até 2500' },
      primary_queries: ['notebook IPS'],
      fallback_queries: ['notebook'],
      must: [
        { field: 'category', operator: 'eq', value: 'notebook' },
        { field: 'panel_type', operator: 'eq', value: 'IPS' },
        { field: 'wifi_bands', operator: 'eq', value: '5GHz' },
        { field: 'price', operator: 'lte', value: 2500, unit: 'BRL' },
      ],
      should: [], must_not: [],
    };
    vi.mocked(api.listSearchDefinitions).mockResolvedValue({ items: [{ id: 'def-1', name: 'Notebook IPS', intent: { category: 'notebook' }, plan }] });
    vi.mocked(api.listSearchSessions).mockResolvedValue({ items: [{ id: 'session-1', intent: plan.intent, plan, messages: [] }] });
    renderPage();

    await waitFor(() => expect(screen.getByText('Entendimento da busca')).toBeInTheDocument());
    expect(screen.getByText('Produto: notebook')).toBeInTheDocument();
    expect(screen.getAllByText(/Tela: IPS/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Wi-Fi: 5 GHz/).length).toBeGreaterThan(0);
    expect(screen.getByText('Consulta na OLX')).toBeInTheDocument();
    expect(screen.getByText('Busca inicial:', { exact: false })).toHaveTextContent('notebook IPS');
    expect(screen.getByText('Ampliação se necessário:', { exact: false })).toHaveTextContent('notebook');
    expect(screen.getByText('Abrir no Pipeline')).toBeInTheDocument();
    expect(screen.queryByText('Executar busca')).not.toBeInTheDocument();
  });

  it('includes one stable client request id in each message attempt', async () => {
    vi.mocked(api.listSearchDefinitions).mockResolvedValue({ items: [] });
    vi.mocked(api.listSearchSessions).mockResolvedValue({ items: [{ id: 'session-message', intent: {}, plan: {}, messages: [] }] });
    vi.mocked(api.sendSearchMessage).mockResolvedValue({ status: 'ok' });
    renderPage();
    await waitFor(() => expect(screen.getByLabelText('Mensagem da busca')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Mensagem da busca'), { target: { value: 'Ajuste o preço máximo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }));
    await waitFor(() => expect(api.sendSearchMessage).toHaveBeenCalledWith('session-message', expect.objectContaining({ client_request_id: expect.stringMatching(/^web-search-/) })));
  });

  it('renders history sidebar on the right with brief model-generated titles and ellipsis', async () => {
    const session = {
      id: 'session-2',
      saved_definition_name: 'Notebook · Tela IPS · até R$ 2.400',
      intent: { category: 'notebook' },
      plan: {},
      messages: [],
    };
    vi.mocked(api.listSearchDefinitions).mockResolvedValue({ items: [] });
    vi.mocked(api.listSearchSessions).mockResolvedValue({ items: [session] });
    renderPage();

    await waitFor(() => expect(screen.getByRole('complementary', { name: 'Histórico de buscas' })).toBeInTheDocument());
    const sidebar = screen.getByRole('complementary', { name: 'Histórico de buscas' });
    await waitFor(() => expect(sidebar).toHaveTextContent('Notebook · Tela IPS · até R$ 2.400'));
    const titleElement = sidebar.querySelector('.truncate');
    expect(titleElement).not.toBeNull();
    expect(titleElement).toHaveTextContent('Notebook · Tela IPS · até R$ 2.400');
  });
});
