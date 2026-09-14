import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import { ProfileGate, ProfileSwitcher, useProfile } from '../profile';

vi.mock('../api', async () => {
  const original = await vi.importActual<typeof import('../api')>('../api');
  return { ...original, fetcher: vi.fn() };
});

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function Probe({ personal = false }: { personal?: boolean }) {
  const profile = useProfile();
  const personalQuery = useQuery({
    queryKey: ['profile-probe', profile.profileId],
    queryFn: () => api.fetcher('/dashboard'),
    enabled: personal && profile.isReady,
  });
  return <div><span data-testid="profile-id">{profile.profileId || 'none'}</span>{personal && <span data-testid="personal-state">{personalQuery.isSuccess ? 'loaded' : 'pending'}</span>}</div>;
}

function renderGate(child: React.ReactNode) {
  const queryClient = client();
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><ProfileGate queryClient={queryClient}>{child}</ProfileGate></MemoryRouter></QueryClientProvider>);
}

describe('profile bootstrap and isolation', () => {
  afterEach(() => {
    api.setActiveProfile(null);
    globalThis.localStorage.clear();
    vi.clearAllMocks();
  });

  it('restores the last valid local profile after a browser refresh', async () => {
    globalThis.localStorage.setItem('gridscout.active-profile-id', 'profile-a');
    vi.mocked(api.fetcher).mockResolvedValue({ items: [{ id: 'profile-a', name: 'Alice' }] });

    renderGate(<Probe personal />);

    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('profile-a'));
    expect(screen.queryByRole('heading', { name: 'Perfis de Pesquisa' })).not.toBeInTheDocument();
  });

  it('blocks personal queries until a profile has been selected', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string) => {
      if (url === '/profiles') return { items: [{ id: 'profile-a', name: 'Alice' }] };
      return { ok: true };
    });

    renderGate(<Probe personal />);
    expect(screen.queryByTestId('profile-id')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: /Alice/ })).toBeInTheDocument());
    expect(screen.queryByTestId('profile-id')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Alice/ }));
    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('profile-a'));
    expect(vi.mocked(api.fetcher).mock.calls.some(([url]) => url === '/profiles')).toBe(true);
    expect(vi.mocked(api.fetcher).mock.calls.some(([url]) => url === '/dashboard')).toBe(true);
  });

  it('lists and creates a profile before rendering', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/profiles' && !options?.method) return { profiles: [] };
      return {};
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'created-profile', name: 'Principal' }), { status: 201, headers: { 'Content-Type': 'application/json' } })));
    renderGate(<Probe />);
    await waitFor(() => expect(screen.getByLabelText('Criar novo perfil')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Criar novo perfil'), { target: { value: 'Principal' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar e continuar' }));
    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('created-profile'));
    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe('/api/v1/profiles');
  });

  it('switches profile without retaining the previous query cache', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({ items: [{ id: 'profile-a', name: 'Alice' }, { id: 'profile-b', name: 'Bob' }] });
    const queryClient = client();
    render(<QueryClientProvider client={queryClient}><MemoryRouter><ProfileGate queryClient={queryClient}><><ProfileSwitcher /><Probe /></></ProfileGate></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.getByRole('button', { name: /Alice/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Alice/ }));
    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('profile-a'));
    queryClient.setQueryData(['personal', 'profile-a'], { stale: true });
    fireEvent.change(screen.getByRole('combobox', { name: 'Perfil ativo' }), { target: { value: 'profile-b' } });
    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('profile-b'));
    expect(queryClient.getQueryData(['personal', 'profile-a'])).toBeUndefined();
  });

  it('returns to profile selection screen when clicking Trocar perfil', async () => {
    vi.mocked(api.fetcher).mockResolvedValue({ items: [{ id: 'profile-a', name: 'Alice' }, { id: 'profile-b', name: 'Bob' }] });
    const queryClient = client();
    render(<QueryClientProvider client={queryClient}><MemoryRouter><ProfileGate queryClient={queryClient}><><ProfileSwitcher /><Probe /></></ProfileGate></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.getByRole('button', { name: /Alice/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Alice/ }));
    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('profile-a'));
    fireEvent.click(screen.getByRole('button', { name: 'Trocar perfil' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Alice/ })).toBeInTheDocument());
    expect(screen.queryByTestId('profile-id')).not.toBeInTheDocument();
  });

  it('allows selecting an icon and color when creating a profile', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/profiles' && !options?.method) return { profiles: [] };
      return {};
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'custom-profile',
      name: 'Custom',
      preferences: { icon: 'zap', color: 'emerald' },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } })));

    renderGate(<Probe />);
    await waitFor(() => expect(screen.getByLabelText('Criar novo perfil')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Criar novo perfil'), { target: { value: 'Custom' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ícone zap' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cor emerald' }));
    fireEvent.click(screen.getByRole('button', { name: 'Criar e continuar' }));

    await waitFor(() => expect(screen.getByTestId('profile-id')).toHaveTextContent('custom-profile'));
    const fetchCalls = vi.mocked(globalThis.fetch).mock.calls;
    const createCall = fetchCalls.find(([url]) => url === '/api/v1/profiles');
    expect(createCall).toBeDefined();
    const payload = JSON.parse(createCall![1]?.body as string);
    expect(payload.preferences.icon).toBe('zap');
    expect(payload.preferences.color).toBe('emerald');
  });

  it('allows editing an existing profile icon and color', async () => {
    vi.mocked(api.fetcher).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/profiles' && !options?.method) {
        return { items: [{ id: 'profile-edit', name: 'Original', preferences: { icon: 'user', color: 'sky' } }] };
      }
      return {};
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'profile-edit',
      name: 'Edited',
      preferences: { icon: 'cpu', color: 'amber' },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    renderGate(<Probe />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Editar perfil' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Editar perfil' }));

    expect(screen.getByLabelText('Alterar perfil')).toHaveValue('Original');
    fireEvent.change(screen.getByLabelText('Alterar perfil'), { target: { value: 'Edited' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ícone cpu' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cor amber' }));
    fireEvent.submit(screen.getByRole('button', { name: 'Salvar alterações' }).closest('form')!);

    await waitFor(() => expect(screen.getByRole('button', { name: /Edited/ })).toBeInTheDocument());
  });
});
