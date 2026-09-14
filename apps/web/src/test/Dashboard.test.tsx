import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Dashboard from '../pages/Dashboard';
import { vi, describe, it, expect } from 'vitest';
import * as api from '../api';
import { mockDashboardData } from '../mocks/data';

vi.mock('../api', () => ({
  fetcher: vi.fn(),
}));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
});

describe('Dashboard', () => {
  it('renders dashboard with mock data', async () => {
    vi.mocked(api.fetcher).mockResolvedValue(mockDashboardData);
    
    render(
      <QueryClientProvider client={queryClient}>
        <Dashboard />
      </QueryClientProvider>
    );

    expect(screen.getByText('Carregando painel...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Anúncios Ativos')).toBeInTheDocument();
    });

    expect(screen.getByText('182')).toBeInTheDocument();
    expect(screen.getAllByText('NVIDIA GeForce RTX 3080 10GB').length).toBeGreaterThan(0);
    expect(screen.getAllByText('84.5').length).toBeGreaterThan(0);
  });
});
