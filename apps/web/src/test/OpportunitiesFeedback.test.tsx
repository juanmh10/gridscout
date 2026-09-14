import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import Opportunities from '../pages/Opportunities';
import * as api from '../api';

vi.mock('../api', async () => {
  const original = await vi.importActual<typeof import('../api')>('../api');
  return { ...original, fetcher: vi.fn() };
});

describe('feedback de oportunidades', () => {
  it('sends a structured positive feedback and optional reason', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.startsWith('/opportunities?')) return { items: [{ id: 'opp-1', product_name: 'RTX 3080', final_score: 88.2, objective_score: 91, request_match_score: 84, preference_affinity: 79, personalized_score: 87, personalization_explanation: 'Compatível com seu orçamento', profile_feedback: { value: 'down' }, price_edge: 0.2, heat_band: 'HOT', asking_price: 2000, delivery_status: 'UNKNOWN', investigation: {} }], total: 1, pages: 1 };
      return {};
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Opportunities /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByText('RTX 3080')).toBeInTheDocument());
    expect(screen.getByText(/request_match: 84/)).toBeInTheDocument();
    expect(screen.getByText(/personalized: 87/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mostrar análise de RTX 3080' }));
    expect(screen.getByText('Compatível com seu orçamento')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Motivo do feedback de RTX 3080' }), { target: { value: 'price' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gostei de RTX 3080' }));
    await waitFor(() => expect(api.fetcher).toHaveBeenCalledWith('/opportunities/opp-1/feedback', expect.objectContaining({ method: 'PUT', body: expect.stringContaining('"value":"up"') })));
    const request = vi.mocked(api.fetcher).mock.calls.find(([url]) => url === '/opportunities/opp-1/feedback');
    expect(request?.[1]?.body).toEqual(expect.stringContaining('price'));
  });

  it('refetches using the selected notebook category', async () => {
    const requests: string[] = [];
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      requests.push(url);
      if (url === '/products/categories') {
        return { items: [{ id: 'notebook', name: 'Notebook', product_count: 1 }], total: 1 };
      }
      if (url.startsWith('/opportunities?')) {
        if (url.includes('category=notebook')) {
          return {
            items: [{ id: 'opp-notebook', product_name: 'Dell Latitude 5420', category: 'notebook', final_score: 82, price_edge: 0.18, heat_band: 'NORMAL', asking_price: 2300, delivery_status: 'UNKNOWN', investigation: {} }],
            total: 1,
            pages: 1,
          };
        }
        return { items: [], total: 0, pages: 1 };
      }
      return {};
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Opportunities /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByRole('tab', { name: /Notebook/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: /Notebook/ }));

    await waitFor(() => expect(screen.getByText('Dell Latitude 5420')).toBeInTheDocument());
    expect(requests.some(url => url.includes('/opportunities?') && url.includes('category=notebook'))).toBe(true);
  });

  it('cycles 3-state sort on Preço header (desc -> asc -> default) and handles search filter', async () => {
    const requests: string[] = [];
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      requests.push(url);
      if (url === '/products/categories') {
        return { items: [{ id: 'notebook', name: 'Notebook', product_count: 2 }], total: 1 };
      }
      if (url.startsWith('/opportunities?')) {
        return {
          items: [
            {
              id: 'opp-nb-1',
              product_name: 'MacBook Air M1 16GB',
              category: 'notebook',
              asking_price: 3679.46,
              estimated_clearing_value: 4794.95,
              final_score: 89.1,
              price_edge: 0.23,
              heat_band: 'HOT',
              delivery_status: 'AVAILABLE',
              investigation: {},
            },
            {
              id: 'opp-nb-2',
              product_name: 'MacBook Air M1 8GB',
              category: 'notebook',
              asking_price: 2146.14,
              estimated_clearing_value: 4021.97,
              final_score: 85.0,
              price_edge: 0.46,
              heat_band: 'NORMAL',
              delivery_status: 'UNKNOWN',
              investigation: {},
            },
          ],
          total: 2,
          pages: 1,
        };
      }
      return {};
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Opportunities /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByText('MacBook Air M1 16GB')).toBeInTheDocument());

    // Verify real liquidation values are rendered (not stuck on 1850)
    expect(screen.getByText('Liq.: R$ 4.794,95')).toBeInTheDocument();
    expect(screen.getByText('Liq.: R$ 4.021,97')).toBeInTheDocument();

    const precoHeader = screen.getByRole('columnheader', { name: /Preço/i });

    // 1st click: price_desc (maior preço)
    fireEvent.click(screen.getByRole('columnheader', { name: /Preço/i }));
    await waitFor(() => {
      expect(requests.some(url => url.includes('sort_by=price_desc'))).toBe(true);
    });

    // 2nd click: price_asc (menor preço)
    fireEvent.click(screen.getByRole('columnheader', { name: /Preço/i }));
    await waitFor(() => {
      expect(requests.some(url => url.includes('sort_by=price_asc'))).toBe(true);
    });

    // 3rd click: resets to default (score_desc)
    fireEvent.click(precoHeader);
    await waitFor(() => {
      const lastRequest = requests[requests.length - 1];
      expect(lastRequest).toContain('sort_by=score_desc');
    });

    // Test search filter
    const searchInput = screen.getByPlaceholderText(/Buscar por produto/i);
    fireEvent.change(searchInput, { target: { value: 'MacBook' } });
    await waitFor(() => {
      expect(requests.some(url => url.includes('search=MacBook'))).toBe(true);
    });

    // Test clear filters button
    const clearButton = screen.getByRole('button', { name: /Limpar filtros ativos/i });
    expect(clearButton).toBeInTheDocument();
    fireEvent.click(clearButton);
    expect(searchInput).toHaveValue('');
  });

  it('filters by date recency, cycles 3-state date sort, and toggles collapsible vertical category menu', async () => {
    const requests: string[] = [];
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      requests.push(url);
      if (url === '/products/categories') {
        return { items: [{ id: 'notebook', name: 'Notebook', product_count: 5 }], total: 1 };
      }
      if (url.startsWith('/opportunities?')) {
        return {
          items: [
            {
              id: 'opp-date-1',
              product_name: 'Dell Latitude Recente',
              category: 'notebook',
              asking_price: 2500,
              estimated_clearing_value: 2900,
              final_score: 88,
              price_edge: 0.15,
              heat_band: 'NORMAL',
              delivery_status: 'AVAILABLE',
              first_seen: new Date().toISOString(),
              investigation: {},
            },
          ],
          total: 1,
          pages: 1,
        };
      }
      return {};
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Opportunities /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByText('Dell Latitude Recente')).toBeInTheDocument());

    // 1. Check date recency text is formatted (shows "Hoje, ...")
    expect(screen.getByText(/Hoje,/i)).toBeInTheDocument();

    // 2. Test date filter dropdown
    const dateSelect = screen.getByLabelText(/Filtrar por data/i);
    expect(dateSelect).toBeInTheDocument();
    fireEvent.change(dateSelect, { target: { value: '1' } });
    await waitFor(() => {
      expect(requests.some(url => url.includes('days=1'))).toBe(true);
    });

    // 3. Test 3-state date sort
    const dateHeader = screen.getByRole('columnheader', { name: /Data/i });
    expect(dateHeader).toBeInTheDocument();

    // 1st click: date_desc (mais recentes primeiro)
    fireEvent.click(dateHeader);
    await waitFor(() => {
      expect(requests.some(url => url.includes('sort_by=date_desc'))).toBe(true);
    });

    // 2nd click: date_asc (mais antigas primeiro)
    fireEvent.click(screen.getByRole('columnheader', { name: /Data/i }));
    await waitFor(() => {
      expect(requests.some(url => url.includes('sort_by=date_asc'))).toBe(true);
    });

    // 3rd click: resets to default (score_desc)
    fireEvent.click(screen.getByRole('columnheader', { name: /Data/i }));
    await waitFor(() => {
      const lastRequest = requests[requests.length - 1];
      expect(lastRequest).toContain('sort_by=score_desc');
    });

    // 4. Test collapsible vertical category menu toggle
    const categoryToggle = screen.getByRole('button', { name: /Expandir menu vertical/i });
    expect(categoryToggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(categoryToggle);
    expect(screen.getByRole('button', { name: /Recolher menu/i })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: /Recolher menu/i }));
    expect(screen.getByRole('button', { name: /Expandir menu vertical/i })).toHaveAttribute('aria-expanded', 'false');
  });
});
