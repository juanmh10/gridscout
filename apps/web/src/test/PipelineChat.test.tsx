import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PipelineChat from '../pages/PipelineChat';
import * as api from '../api';

vi.mock('../api', () => ({ fetcher: vi.fn(), ApiError: class ApiError extends Error {} }));

function renderChat() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/pipelines/pipe-1/chat']}>
        <Routes>
          <Route path="/pipelines/:pipelineId/chat" element={<PipelineChat />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('PipelineChat', () => {
  beforeEach(() => vi.clearAllMocks());

  it('starts the persisted overview and renders the first candidate batch', async () => {
    let overviewCreated = false;
    const message = {
      id: 'message-1', role: 'assistant', kind: 'overview', content: 'Resumo da execução.',
      candidates: [{ id: 'listing-1', title: 'RTX 3080', product_name: 'NVIDIA RTX 3080', price: 2100, condition: 'good', final_score: 91, price_edge: 0.16, reason: 'Preço abaixo da referência.' }],
      citations: [], actions: [], created_at: '2026-08-25T12:00:00Z', metadata: {},
    };
    vi.mocked(api.fetcher).mockImplementation(async (_url: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        overviewCreated = true;
        return { message, thread: { id: 'thread-1', next_offset: 0, total_candidates: 1 }, available_actions: [{ action: 'opportunity_summary', label: 'Oportunidades encontradas' }, { action: 'next_batch', label: 'Ver próximos 10' }, { action: 'market_check', label: 'Comparar mercado externo' }], products: [{ id: 'product-1', display_name: 'NVIDIA RTX 3080' }] } as never;
      }
      return {
        eligibility: { available: true, mode: 'gemini' },
        thread: overviewCreated ? { id: 'thread-1', next_offset: 0, total_candidates: 1 } : null,
        messages: overviewCreated ? [message] : [],
        available_actions: [{ action: 'overview', label: 'Resumo da execução' }, { action: 'opportunity_summary', label: 'Oportunidades encontradas' }, { action: 'next_batch', label: 'Ver próximos 10' }, { action: 'market_check', label: 'Comparar mercado externo' }, { action: 'ask', label: 'Fazer uma pergunta' }],
        products: [{ id: 'product-1', display_name: 'NVIDIA RTX 3080' }],
      } as never;
    });

    renderChat();

    await waitFor(() => expect(screen.getByText('Resumo da execução.')).toBeInTheDocument());
    expect(screen.getByText('RTX 3080')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ver próximos 10' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Comparar mercado externo' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Medir mercado de/ })).not.toBeInTheDocument();
    expect(api.fetcher).toHaveBeenCalledWith('/pipelines/pipe-1/chat/messages', expect.objectContaining({ method: 'POST' }));
  });

  it('shows the safe unavailability message for an internal failure', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({
      eligibility: { available: false, mode: 'unavailable', reason_message: 'A análise não está disponível para esta execução.' },
      thread: null, messages: [], available_actions: [], products: [],
    } as never);

    renderChat();

    expect(await screen.findByText('A análise não está disponível para esta execução.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Pergunta sobre o pipeline' })).not.toBeInTheDocument();
  });
});
