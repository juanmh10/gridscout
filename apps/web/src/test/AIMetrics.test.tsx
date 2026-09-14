import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import AIMetrics from '../pages/AIMetrics';
import * as api from '../api';

vi.mock('../api', () => ({ fetcher: vi.fn() }));

describe('AIMetrics', () => {
  it('uses the 24h contract and renders summary, groups and recent calls', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url.startsWith('/model-metrics/summary')) {
        return {
          window: '24h',
          from: '2026-08-23T00:00:00Z',
          to: '2026-08-24T00:00:00Z',
          currency: 'USD',
          estimated_cost_usd: null,
          calls: 12,
          success_rate: 0.9,
          fallback_rate: 0.1,
          latency_ms: { average: 240, p50: 180, p95: 490 },
          tokens: { prompt: 100, candidates: 200, thoughts: 30, cached: 20, tool: 10, total: 360 },
          by_model: [{ model_id: 'gemini-2.5-flash', calls: 12, estimated_cost_usd: 0.002, average_latency_ms: 240, success_rate: 0.9, tokens_total: 360 }],
          by_operation: [{ operation: 'normalize_listing', calls: 12, estimated_cost_usd: null, average_latency_ms: 240 }],
        };
      }
      return {
        items: [{
          id: 'call-1',
          pipeline_run_id: 'run-1',
          origin: 'pipeline',
          operation: 'normalize_listing',
          provider: 'vertex',
          auth_mode: 'service_account',
          model_id: 'gemini-2.5-flash',
          status: 'success',
          started_at: '2026-08-24T12:00:00Z',
          duration_ms: 240,
          prompt_tokens: 100,
          candidates_tokens: 200,
          thoughts_tokens: 30,
          cached_tokens: 20,
          tool_tokens: 10,
          total_tokens: 360,
          finish_reason: 'stop',
          estimated_cost_usd: 0.002,
          pricing_version: 'paid-standard-v1',
          error_code: null,
        }],
        total: 1,
        page: 1,
        page_size: 50,
        pages: 1,
      };
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AIMetrics /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByText('Estimativa paid standard tier USD')).toBeInTheDocument());
    expect(screen.getAllByText('Preço não configurado').length).toBeGreaterThan(0);
    expect(screen.getByText('Estimativa paid standard tier USD')).toBeInTheDocument();
    expect(screen.getAllByText('normalize_listing').length).toBeGreaterThan(0);
    expect(screen.getAllByText('gemini-2.5-flash').length).toBeGreaterThan(0);
    expect(screen.getByText('Chamadas recentes')).toBeInTheDocument();
    expect(api.fetcher).toHaveBeenCalledWith('/model-metrics/summary?window=24h');
    expect(api.fetcher).toHaveBeenCalledWith('/model-metrics/calls?page=1&page_size=50&window=24h');
  });
});
