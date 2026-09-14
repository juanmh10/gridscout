import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import Opportunities from '../pages/Opportunities';
import Market from '../pages/Market';
import Listings from '../pages/Listings';
import Pipelines from '../pages/Pipelines';
import Benchmarks from '../pages/Benchmarks';
import SettingsPage from '../pages/Settings';
import IncompleteDiscoveries from '../pages/IncompleteDiscoveries';
import { canonicalSourceUrl } from '../lib/format';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import * as api from '../api';

vi.mock('../api', () => ({
  fetcher: vi.fn(),
  getApiErrorDetail: vi.fn(),
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

describe('Frontend Pages Suite', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders partial cards and queues their constrained agent analysis', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (_url: string, options?: RequestInit) => {
      if (options?.method === 'POST') return { queued: 1, total: 1 };
      return {
        items: [{
          title: 'Notebook Dell Latitude 5420',
          price: 2500,
          location: 'São Paulo',
          source_url: 'https://example.test/notebook',
          observed_at: '2026-08-29T01:00:00Z',
          analysis: {
            status: 'completed', category: 'notebook', brand: 'Dell', model_name: 'Latitude 5420',
            confidence: 0.88, summary: 'Identidade baseada no card.', attributes: [{ name: 'ram_gb', value: '16' }],
          },
          missing_evidence: ['Vendedor', 'Condição'],
          evidence_scope: 'Somente card de busca; campos ausentes permanecem não verificados.',
        }],
        summary: { total: 1, completed: 1, pending: 0, running: 0, fallback: 0, failed: 0 },
        page: 1,
        page_size: 24,
        pages: 1,
      };
    });

    render(<QueryClientProvider client={createTestQueryClient()}><IncompleteDiscoveries /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByText('Notebook Dell Latitude 5420')).toBeInTheDocument());
    expect(screen.getByText('Evidência parcial')).toBeInTheDocument();
    expect(screen.getByText(/Não verificado: Vendedor, Condição/)).toBeInTheDocument();
    expect(screen.queryByText('incomplete-card')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Processar com agentes' }));
    await waitFor(() => expect(vi.mocked(api.fetcher).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(true));
  });

  it('renders Opportunities page with items', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      items: [
        {
          id: 'opp-001',
          product_name: 'NVIDIA GeForce RTX 3080 10GB',
          category: 'gpu',
          asking_price: 2100.0,
          price_edge: 0.222,
          final_score: 88.4,
          heat_band: 'HOT',
          source_url: 'https://www.olx.com.br/d/anuncio/rtx-3080',
        }
      ],
      total: 1,
      page: 1,
      page_size: 20,
      pages: 1
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Opportunities />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('NVIDIA GeForce RTX 3080 10GB')).toBeInTheDocument();
    });
    expect(screen.getByText('88.4')).toBeInTheDocument();
    expect(screen.getByText('22,2%')).toBeInTheDocument();
    expect(screen.getByText('HOT')).toBeInTheDocument();
    expect(screen.getByText('R$ 2.100,00')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Abrir fonte de NVIDIA GeForce RTX 3080 10GB' })).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: 'Abrir fonte de NVIDIA GeForce RTX 3080 10GB' })).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('renders Market analysis with real OLX categories and no mock products', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.includes('/products/categories')) {
        return {
          items: [
            { id: 'gpu', name: 'Placas de vídeo', product_count: 1 },
            { id: 'cpu', name: 'Processadores', product_count: 0 },
          ],
          total: 2,
        };
      }
      if (url.startsWith('/products?')) {
        return {
          items: [
            { id: 'prod-gpu-rtx3080', display_name: 'NVIDIA GeForce RTX 3080 10GB', category: 'gpu', active_listings_count: 14 }
          ],
          total: 1,
          page: 1,
          page_size: 10,
          pages: 1
        };
      }
      if (url.includes('/market')) {
        return {
          product_name: 'NVIDIA GeForce RTX 3080 10GB',
          sample_size: 28,
          confidence: 0.94,
          heat_band: 'HOT',
          market_heat: 84.5,
          asking_median: 2850.0,
          estimated_clearing_value: 2700.0,
          fast_sale_value: 2350.0,
          p10: 2200.0,
          p25: 2550.0,
          median: 2850.0,
          p75: 3100.0,
          p90: 3400.0,
          mad: 280.0
        };
      }
      return {};
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Market />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByRole('tab', { name: /Placas de vídeo/ })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /Placas de vídeo/ })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByText('NVIDIA GeForce RTX 3080 10GB')).toBeInTheDocument();
    expect(screen.queryByText('Mockada')).not.toBeInTheDocument();
  });

  it('renders Market notebook filters, prices on front, link access, and collapsible sections', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.includes('/products/categories')) {
        return {
          items: [
            { id: 'notebook', name: 'Notebooks', product_count: 2 },
          ],
          total: 1,
        };
      }
      if (url.includes('/products/facets')) {
        return {
          category: 'notebook',
          category_label: 'Notebooks',
          facets: [
            {
              key: 'panel_type',
              label: 'Painel da Tela',
              type: 'select',
              options: [
                { value: 'IPS', label: 'IPS / WVA' },
                { value: 'OLED', label: 'OLED' },
              ],
            },
          ],
        };
      }
      if (url.startsWith('/products?')) {
        return {
          items: [
            {
              id: 'catalog-notebook-001',
              display_name: 'Lenovo LOQ 15IRH8 83EU0001BR',
              category: 'notebook',
              active_listings_count: 2,
              market_median_price: 4499.0,
              source_url: 'https://loja.lenovo.com/notebook-loq-15',
              attributes: {
                cpu_brand: 'Intel',
                cpu_model: 'Core i5-12450H',
                ram_gb: 16,
                storage_gb: 512,
                panel_type: 'IPS',
                screen_ips: true,
              },
            },
          ],
          total: 1,
          page: 1,
          page_size: 50,
          pages: 1,
        };
      }
      if (url.includes('/market')) {
        return {
          product_name: 'Lenovo LOQ 15IRH8 83EU0001BR',
          sample_size: 5,
          confidence: 0.95,
          heat_band: 'HOT',
          market_heat: 88.0,
          asking_median: 4499.0,
          estimated_clearing_value: 4200.0,
          fast_sale_value: 3900.0,
          p10: 3800.0,
          p25: 4100.0,
          median: 4499.0,
          p75: 4700.0,
          p90: 4900.0,
          mad: 250.0,
          included_count: 4,
          unverified_count: 1,
          excluded_count: 0,
        };
      }
      if (url.includes('/listings?product_id=')) {
        return {
          items: [
            {
              id: 'list-nb-01',
              title: 'Lenovo LOQ i5 16GB 512GB Na Caixa Garantia',
              price: 4250.0,
              source: 'olx',
              source_url: 'https://www.olx.com.br/d/anuncio/lenovo-loq-4250',
              location_city: 'São Paulo',
              location_state: 'SP',
              condition: 'like_new',
              status: 'active',
            },
          ],
          total: 1,
        };
      }
      return {};
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Market />
      </QueryClientProvider>
    );

    // 1. Check title and filter elements
    await waitFor(() => {
      expect(screen.getByText('Filtros de Busca')).toBeInTheDocument();
      expect(screen.getByPlaceholderText('Ex: Lenovo LOQ, Dell, Vivobook...')).toBeInTheDocument();
      expect(screen.getByText('Painel da Tela')).toBeInTheDocument();
      expect(screen.getByText('Lenovo LOQ 15IRH8 83EU0001BR')).toBeInTheDocument();
    });

    // 2. Check product displayed on front with price
    expect(screen.getByText('R$ 4.499,00')).toBeInTheDocument();
    expect(screen.getByText(/Core i5-12450H/)).toBeInTheDocument();
    expect(screen.getByText(/16GB/)).toBeInTheDocument();

    // 3. Check direct store link button for the product
    await waitFor(() => {
      const productLink = screen.getByRole('link', { name: /Acessar link do produto/i });
      expect(productLink).toHaveAttribute('href', 'https://loja.lenovo.com/notebook-loq-15');
      expect(productLink).toHaveAttribute('target', '_blank');
    });

    // 4. Check listings section and direct link access
    await waitFor(() => {
      expect(screen.getByText('Lenovo LOQ i5 16GB 512GB Na Caixa Garantia')).toBeInTheDocument();
      expect(screen.getByText('R$ 4.250,00')).toBeInTheDocument();
    });

    const listingAccessLink = screen.getByRole('link', { name: /Acessar link do anúncio Lenovo LOQ/i });
    expect(listingAccessLink).toHaveAttribute('href', 'https://www.olx.com.br/d/anuncio/lenovo-loq-4250');
    expect(listingAccessLink).toHaveAttribute('target', '_blank');

    // 5. Test interaction with sort by
    const sortBySelect = screen.getByLabelText('Ordenar por');
    expect(sortBySelect).toHaveValue('name_asc');
    fireEvent.change(sortBySelect, { target: { value: 'price_asc' } });
    expect(sortBySelect).toHaveValue('price_asc');

    // 6. Test collapsible section toggle
    const collapseButtons = screen.getAllByRole('button', { name: /Recolher/i });
    expect(collapseButtons.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(collapseButtons[0]); // collapse filters
    expect(screen.getByRole('button', { name: /Expandir/i })).toBeInTheDocument();
  });

  it('keeps Outro visible without inventing market pricing', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.includes('/products/categories')) {
        return { items: [{ id: 'other', name: 'Outro', product_count: 1 }], total: 1 };
      }
      if (url.startsWith('/products?')) {
        return {
          items: url.includes('category=other')
            ? [{ id: 'product-other', display_name: 'Câmera Canon', category: 'other', active_listings_count: 1 }]
            : [],
          total: url.includes('category=other') ? 1 : 0,
        };
      }
      if (url.includes('/products/product-other/market')) {
        return {
          product_name: 'Câmera Canon',
          category: 'other',
          market_eligible: false,
          market_exclusion_reason: 'Classificado como Outro: mantido no histórico da coleta, sem coorte comparável para precificação ou oportunidades.',
        };
      }
      if (url.includes('/listings?product_id=product-other')) return { items: [], total: 0 };
      if (url.includes('/products/facets')) return { category: 'other', category_label: 'Outro', facets: [] };
      return {};
    });

    render(<QueryClientProvider client={createTestQueryClient()}><Market /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByRole('tab', { name: /Outro/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: /Outro/ }));
    await waitFor(() => expect(screen.getByText('Sem coorte de precificação')).toBeInTheDocument());
    expect(screen.getByText(/mantido no histórico da coleta/)).toBeInTheDocument();
    expect(screen.queryByText('Estimativa de liquidação')).not.toBeInTheDocument();
  });

  it('renders Listings page', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      items: [
        {
          id: 'list-101',
          title: 'RTX 3080 EVGA FTW3 Ultra 10GB',
          category: 'gpu',
          condition: 'like_new',
          price: 2100.0,
          status: 'active',
          location_city: 'São Paulo',
          location_state: 'SP'
        }
      ],
      total: 1,
      page: 1,
      page_size: 20,
      pages: 1
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Listings />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('RTX 3080 EVGA FTW3 Ultra 10GB')).toBeInTheDocument();
    });
    expect(screen.getByText('R$ 2.100,00')).toBeInTheDocument();
    expect(screen.getByText('Como novo')).toBeInTheDocument();
  });

  it('renders Pipelines page', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      items: [
        {
          id: 'pipe-001',
          type: 'synthetic_full_ingest',
          status: 'completed',
          started_at: '2026-08-23T15:30:00Z',
          duration_seconds: 12.2,
          processed_count: 200,
          opportunities_found: 14,
          chat: { available: true, mode: 'local' },
        }
      ],
      total: 1,
      page: 1,
      page_size: 20,
      pages: 1
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter>
          <Pipelines />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Execução de pipeline')).toBeInTheDocument();
    });
    expect(screen.getByText('Executar pipeline')).toBeInTheDocument();
    expect(screen.getByText('Execuções recentes')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Abrir análise' })).toHaveAttribute('href', '/pipelines/pipe-001/chat');
  });

  it('renders Stop button for an active running pipeline and posts to cancel endpoint', async () => {
    const cancelCalls: string[] = [];
    vi.mocked(api.fetcher).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes('/cancel') && options?.method === 'POST') {
        cancelCalls.push(url);
        return { status: 'cancelled', message: 'Pipeline cancelado' };
      }
      if (url.startsWith('/pipelines?')) {
        return {
          items: [{
            id: 'pipe-running-1',
            type: 'fixture_ingest',
            status: 'running',
            started_at: '2026-08-30T01:00:00Z',
            duration_seconds: 15,
            processed_count: 2,
            opportunities_found: 0,
            steps: [{ configuration: { search_definition_name: 'Pipeline Ativo' } }],
          }],
          total: 1,
          page: 1,
          page_size: 20,
          pages: 1,
        };
      }
      if (url.startsWith('/search-definitions')) return { items: [] };
      if (url === '/status') return { app_mode: 'local', external_providers: { vertex_ai: 'active' } };
      return {};
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter><Pipelines /></MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Pipeline Ativo')).toBeInTheDocument();
    });

    const stopButton = screen.getByRole('button', { name: /Parar/i });
    expect(stopButton).toBeInTheDocument();
    fireEvent.click(stopButton);

    await waitFor(() => {
      expect(cancelCalls.length).toBe(1);
      expect(cancelCalls[0]).toContain('/pipelines/pipe-running-1/cancel');
    });
  });

  it('blocks an OLX scope before enqueueing when its navigation budget is unavailable', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.startsWith('/pipelines?')) return { items: [], total: 0, page: 1, page_size: 20, pages: 1 };
      if (url === '/search-definitions') return {
        items: [{
          id: 'definition-1', name: 'Notebook IPS',
          plan: {
            desired_count: 5,
            max_results: 15,
            primary_queries: ['notebook ips'],
            fallback_queries: [],
            must: [{ field: 'category', value: 'notebook' }],
          },
        }],
      };
      if (url === '/status') return { app_mode: 'live' };
      if (url === '/marketplace/auth/status') return { authenticated: true, session_state: 'connected' };
      if (url === '/marketplace/rate-limit') return {
        state: 'closed', limit_per_hour: 60, used: 60, remaining: 0,
        reset_at: '2026-08-26T19:00:00Z', retry_after_seconds: 1354,
        min_interval_seconds: 8, max_interval_seconds: 12,
      };
      if (url.startsWith('/search-scopes?')) return { items: [] };
      return {};
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/pipelines?search_definition=definition-1']}>
          <Pipelines />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByRole('option', { name: 'OLX' })).toBeEnabled());
    fireEvent.change(screen.getByRole('combobox', { name: 'Fonte' }), { target: { value: 'olx' } });

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Orçamento OLX indisponível para este escopo.'));
    expect(screen.getByRole('alert')).toHaveTextContent('São necessárias 17 navegações e há 0 disponíveis.');
    expect(screen.getByRole('button', { name: 'Executar pipeline' })).toBeDisabled();
  });

  it('previews time-based high volume before enqueueing its selected cadence', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.startsWith('/pipelines?')) return { items: [], total: 0, page: 1, page_size: 20, pages: 1 };
      if (url === '/search-definitions') return { items: [] };
      if (url === '/status') return { app_mode: 'local', external_providers: { vertex_ai: 'active' } };
      if (url === '/marketplace/auth/status') return { authenticated: false, session_state: 'disconnected' };
      if (url.startsWith('/search-scopes?')) return { items: [] };
      if (url === '/pipelines/high-volume/preflight') return {
        state: 'ready', can_start: true, duration_minutes: 30, aggressiveness: 'intensive',
        pace_seconds: 65, navigation_cap: 27, discovery_navigation_cap: 20, navigation_reserve: 7,
        estimated_discoveries: { min: 600, max: 900 }, estimated_details_max: 7,
        estimate_basis: 'estimativa conservadora por página', required_to_start: 0,
        card_agent_available: true, estimated_agent_reviews_max: 900,
      };
      if (url === '/pipelines/run') return { pipeline_id: 'pipe-new', status: 'pending' };
      return {};
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter><Pipelines /></MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.change(screen.getByRole('combobox', { name: 'Ritmo' }), { target: { value: 'high_volume' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Consulta' }), { target: { value: 'RTX 3080' } });
    fireEvent.change(screen.getByLabelText('Duração em minutos'), { target: { value: '30' } });
    fireEvent.change(screen.getByLabelText('Agressividade'), { target: { value: 'intensive' } });
    fireEvent.click(screen.getByLabelText('Revisar cards com agente antes de publicar'));

    await waitFor(() => expect(screen.getByText('aprox. 600–900 descobertas · até 7 detalhes')).toBeInTheDocument());
    const execute = screen.getByRole('button', { name: 'Executar pipeline' });
    expect(execute).toBeEnabled();
    fireEvent.click(execute);

    await waitFor(() => expect(vi.mocked(api.fetcher).mock.calls.some(([url]) => url === '/pipelines/run')).toBe(true));
    const runCall = vi.mocked(api.fetcher).mock.calls.find(([url]) => url === '/pipelines/run');
    const payload = JSON.parse(String(runCall?.[1]?.body));
    expect(payload).toMatchObject({
      workload_mode: 'high_volume', duration_minutes: 30, aggressiveness: 'intensive', query: 'RTX 3080', card_review_mode: 'required',
    });
  });

  it('renders Benchmarks page', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      items: [
        {
          id: 'eval-run-001',
          dataset_version: 'v1.0.0',
          configuration: 'deterministic_local_v1',
          executed_at: '2026-08-23T15:45:00Z',
          status: 'completed',
          failures_count: 0,
          metrics: {
            normalization_accuracy: 0.985,
            comparable_precision_at_5: 0.960,
            retrieval_mrr: 0.940,
            price_error_mae: 34.50,
            search_intent_f1: 0.88,
            hard_violations: 2,
            precision_at_10: 0.77,
            recall_at_30: 0.66,
            ndcg_at_10: 0.72,
          },
          benchmark_comparison: {
            compiler_version: 'compiler-2',
            dataset_version: 'v1.0.0',
            strategies: { literal: 0.71, static: 0.75, adaptive: 0.79 },
            promoted_strategy: 'adaptive',
          }
        }
      ],
      total: 1,
      page: 1,
      page_size: 20,
      pages: 1
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <Benchmarks />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('98,5%')).toBeInTheDocument();
    });
    expect(screen.getByText('96,0%')).toBeInTheDocument();
    expect(screen.getByText('0.940')).toBeInTheDocument();
    expect(screen.getByText('R$ 34,50')).toBeInTheDocument();
    expect(screen.getByText('88,0%')).toBeInTheDocument();
    expect(screen.getByText('Violações duras')).toBeInTheDocument();
    expect(screen.getAllByText('compiler-2').length).toBeGreaterThan(0);
    expect(screen.getAllByText('adaptive: 0.79').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Estratégia promovida:').length).toBeGreaterThan(0);
  });

  it('renders Settings page', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      app_mode: 'local',
      synthetic_data: true,
      external_providers: {
        olx: 'disabled',
        vertex_ai: 'disabled',
        vertex_search: 'disabled',
        gemini_embeddings: 'disabled'
      },
      preference_profile: {
        max_capital: 5000.0,
        min_desired_edge: 0.10,
        risk_tolerance: 0.45,
        preferred_location: 'SP'
      }
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <SettingsPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Status do Sistema')).toBeInTheDocument();
    });
    expect(screen.getByText('Preferências do Usuário')).toBeInTheDocument();
    expect(screen.getByText('Salvar Preferências')).toBeInTheDocument();
  });

  it('normalizes OLX URLs to canonical www.olx.com.br/vi/ID format', () => {
    expect(canonicalSourceUrl('https://sp.olx.com.br/sao-paulo-e-regiao/games/consoles-de-video-game/ps5-1530897984')).toBe('https://www.olx.com.br/vi/1530897984');
    expect(canonicalSourceUrl('https://rs.olx.com.br/regioes-de-porto-alegre/games/ps5-1530925563')).toBe('https://www.olx.com.br/vi/1530925563');
    expect(canonicalSourceUrl('https://www.olx.com.br/vi/1530897984')).toBe('https://www.olx.com.br/vi/1530897984');
    expect(canonicalSourceUrl('https://loja.lenovo.com/notebook-loq-15')).toBe('https://loja.lenovo.com/notebook-loq-15');
    expect(canonicalSourceUrl(null)).toBe('');
    expect(canonicalSourceUrl('')).toBe('');
  });
});
