import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, fetcher, setActiveProfile } from '../api';

describe('API error handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setActiveProfile(null);
  });

  it('surfaces FastAPI structured detail messages', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: 'login_window_closed', message: 'A janela foi fechada.' } }), {
        status: 409,
        statusText: 'Conflict',
        headers: { 'Content-Type': 'application/json' },
      }),
    ));

    await expect(fetcher('/marketplace/auth/verify-code')).rejects.toMatchObject({
      name: 'ApiError',
      message: 'A janela foi fechada.',
      status: 409,
      code: 'login_window_closed',
    });
  });

  it('surfaces FastAPI validation detail arrays', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: [{ msg: 'Field required' }, { msg: 'Invalid value' }] }), {
        status: 422,
        statusText: 'Unprocessable Entity',
        headers: { 'Content-Type': 'application/json' },
      }),
    ));

    try {
      await fetcher('/marketplace/auth/start');
      throw new Error('expected fetcher to reject');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).message).toBe('Field required; Invalid value');
    }
  });

  it('uses a useful status fallback for non-JSON errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('upstream unavailable', { status: 503, statusText: 'Service Unavailable' }),
    ));

    await expect(fetcher('/status')).rejects.toMatchObject({
      message: 'upstream unavailable',
      status: 503,
    });
  });

  it('uses the HTTP status when the error body is empty', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('', { status: 504, statusText: 'Gateway Timeout' }),
    ));

    await expect(fetcher('/status')).rejects.toMatchObject({
      message: 'Request failed (504 Gateway Timeout)',
      status: 504,
    });
  });

  it('sends the active profile only on profile-scoped requests', async () => {
    const request = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 })));
    vi.stubGlobal('fetch', request);
    setActiveProfile({ id: 'profile-42', name: 'Compras' });

    await fetcher('/dashboard');
    await fetcher('/profiles');

    expect(request.mock.calls[0][1]?.headers).toMatchObject({ 'X-Profile-ID': 'profile-42' });
    expect(request.mock.calls[1][1]?.headers).not.toHaveProperty('X-Profile-ID');
  });
});

describe('Brasília timezone and location formatting', () => {
  it('formats dates strictly in America/Sao_Paulo (Brasília UTC-3)', async () => {
    const { formatDateTime, formatRecency, cleanLocation } = await import('../lib/format');

    // 17:22 UTC is 14:22 in Brasília
    const dtUTC = '2026-09-03T17:22:42.306333Z';
    expect(formatDateTime(dtUTC)).toContain('14:22:42');
    expect(formatRecency(dtUTC)).toMatch(/Hoje|Ontem|\d{2}\/\d{2}/);

    // Test cleanLocation strips ad publication date suffixes
    expect(cleanLocation('Virgem da Lapa - MG Hoje, 14:12')).toBe('Virgem da Lapa - MG');
    expect(cleanLocation('São Paulo - SP Hoje, 12:57')).toBe('São Paulo - SP');
    expect(cleanLocation('Salvador - BA Ontem, 15:29')).toBe('Salvador - BA');
    expect(cleanLocation('Uberlândia - MG 28 de ago, 18:36')).toBe('Uberlândia - MG');
    expect(cleanLocation('Curitiba - PR')).toBe('Curitiba - PR');
    expect(cleanLocation('')).toBe('Brasil');
    expect(cleanLocation(null)).toBe('Brasil');
  });
});
