import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import Pipelines from '../pages/Pipelines';
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

describe('Pipelines Resume and Workload Metrics', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders resume button for partially completed high-volume run and calls resume endpoint on click', async () => {
    const resumeCalls: string[] = [];

    vi.mocked(api.fetcher).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes('/resume') && options?.method === 'POST') {
        resumeCalls.push(url);
        return { status: 'queued', message: 'Retomando detalhes' };
      }
      if (url.includes('/pipelines')) {
        return {
          items: [{
            id: 'pipe-resume-1',
            type: 'fixture_high_volume_ingest',
            status: 'completed_partial',
            started_at: '2026-08-29T10:00:00Z',
            duration_seconds: 120,
            processed_count: 5,
            snapshots_created: 5,
            products_normalized: 5,
            opportunities_found: 1,
            workload: {
              mode: 'high_volume',
              phase: 'completed_partial',
              discovery_target: 2000,
              observed: 100,
              discovered: 80,
              enrichment_planned: 95,
              enrichment_completed: 5,
              pending_detail_tasks: 90,
              failed_detail_tasks: 0,
              attempts_count: 1,
              navigations_consumed: 45,
              can_resume: true,
            },
            dataset_analysis: {
              status: 'completed',
              observed: { discoveries: 80 },
              capture_quality: { price_usable: { coverage: 0.95 } },
            },
            steps: [{ configuration: { search_definition_name: 'GPU Alto Volume' } }],
            scopes: [],
          }],
          total: 1,
          page: 1,
          page_size: 20,
          pages: 1,
        };
      }
      if (url.includes('/search-definitions')) {
        return { items: [] };
      }
      if (url.includes('/status')) {
        return { app_mode: 'local' };
      }
      return {};
    });

    render(
      <MemoryRouter>
        <QueryClientProvider client={createTestQueryClient()}>
          <Pipelines />
        </QueryClientProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('GPU Alto Volume')).toBeInTheDocument();
    });

    // Check human-readable progress without technical IDs
    expect(screen.getByText(/Descoberta: 80\/2000/)).toBeInTheDocument();
    expect(screen.getByText(/Detalhes processados: 5\/95/)).toBeInTheDocument();
    expect(screen.getByText(/Pendentes: 90/)).toBeInTheDocument();
    expect(screen.getByText(/Tentativas: 1/)).toBeInTheDocument();

    // Find and click resume button
    const resumeBtn = screen.getByRole('button', { name: /Retomar detalhes pendentes/i });
    expect(resumeBtn).toBeInTheDocument();

    fireEvent.click(resumeBtn);

    await waitFor(() => {
      expect(resumeCalls.length).toBe(1);
      expect(resumeCalls[0]).toContain('/pipelines/pipe-resume-1/resume');
    });
  });
});
