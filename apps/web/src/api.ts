export const API_BASE = '/api/v1';

/**
 * The profile is deliberately kept in a module variable.  It is an execution
 * context, not an authentication token, and must never be persisted in a
 * browser storage API.
 */
export interface Profile {
  id: string;
  name?: string;
  display_name?: string;
  label?: string;
  is_active?: boolean;
  active?: boolean;
  preferences?: Record<string, any>;
  [key: string]: unknown;
}

let activeProfile: Profile | null = null;

export function getActiveProfile(): Profile | null {
  return activeProfile;
}

export function setActiveProfile(profile: Profile | null): void {
  activeProfile = profile;
}

export function getActiveProfileId(): string | null {
  return activeProfile?.id || null;
}

/**
 * Personal endpoints are always scoped by the currently selected profile.
 * Profile discovery/creation is intentionally excluded so the bootstrap can
 * happen before an active profile exists.
 */
function shouldSendProfileHeader(url: string): boolean {
  const path = url.split('?')[0].replace(/^\//, '');
  return Boolean(activeProfile?.id) && path !== 'health' && path !== 'status' && path !== 'profiles' && !path.startsWith('profiles/');
}

export class ApiError extends Error {
  status: number;
  code?: string;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

export interface RateLimitErrorDetail {
  message?: string;
  retry_after_seconds?: number | null;
  reset_at?: string | null;
  remaining?: number | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null;
}

function errorMessage(payload: unknown, status: number, statusText: string): { message: string; code?: string } {
  const body = record(payload);
  const detail = body?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return { message: detail };
  }

  const detailRecord = record(detail);
  if (detailRecord && typeof detailRecord.message === 'string') {
    return {
      message: detailRecord.message,
      code: typeof detailRecord.code === 'string'
        ? detailRecord.code
        : typeof body?.code === 'string'
          ? body.code
          : typeof body?.error_code === 'string'
            ? body.error_code
            : undefined,
    };
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map(item => {
        const itemRecord = record(item);
        if (itemRecord && typeof itemRecord.msg === 'string') return itemRecord.msg;
        return typeof item === 'string' ? item : '';
      })
      .filter(Boolean);
    if (messages.length) return { message: messages.join('; ') };
  }

  const error = record(body?.error);
  if (error && typeof error.message === 'string') {
    return { message: error.message, code: typeof error.code === 'string' ? error.code : undefined };
  }
  if (typeof body?.message === 'string' && body.message.trim()) {
    return { message: body.message };
  }
  if (typeof payload === 'string' && payload.trim()) {
    return { message: payload.trim() };
  }

  return { message: `Request failed (${status}${statusText ? ` ${statusText}` : ''})` };
}

export function getApiErrorDetail(error: unknown): RateLimitErrorDetail | null {
  if (!(error instanceof ApiError)) return null;
  const body = record(error.payload);
  const detail = record(body?.detail) || record(body?.error);
  if (!detail) return null;

  return {
    message: typeof detail.message === 'string' ? detail.message : undefined,
    retry_after_seconds: typeof detail.retry_after_seconds === 'number' ? detail.retry_after_seconds : null,
    reset_at: typeof detail.reset_at === 'string' ? detail.reset_at : null,
    remaining: typeof detail.remaining === 'number' ? detail.remaining : null,
  };
}

export async function fetcher<T>(url: string, options?: RequestInit): Promise<T> {
  const requestHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (options?.headers instanceof Headers) {
    options.headers.forEach((value, key) => { requestHeaders[key] = value; });
  } else if (Array.isArray(options?.headers)) {
    options.headers.forEach(([key, value]) => { requestHeaders[key] = value; });
  } else if (options?.headers) {
    Object.assign(requestHeaders, options.headers);
  }
  if (shouldSendProfileHeader(url)) requestHeaders['X-Profile-ID'] = activeProfile!.id;

  const res = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: requestHeaders,
  });

  if (!res.ok) {
    let payload: unknown = null;
    let responseText = '';
    try {
      responseText = await res.text();
      payload = responseText ? JSON.parse(responseText) : null;
    } catch {
      payload = responseText;
    }
    const parsed = errorMessage(payload, res.status, res.statusText);
    throw new ApiError(parsed.message, res.status, payload, parsed.code);
  }

  // DELETE and a few mutation implementations return 204 without a body.
  if (res.status === 204) return undefined as T;
  const responseText = await res.text();
  if (!responseText) return undefined as T;
  try {
    return JSON.parse(responseText) as T;
  } catch {
    return responseText as T;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null;
}

export function normalizeProfiles(payload: unknown): Profile[] {
  const record = asRecord(payload);
  const values = Array.isArray(payload)
    ? payload
    : Array.isArray(record?.items)
      ? record.items
      : Array.isArray(record?.profiles)
        ? record.profiles
        : record?.profile
        ? [record.profile]
          : (typeof record?.id === 'string' || typeof record?.profile_id === 'string')
            ? [record]
          : [];
  return values
    .map(value => {
      const item = asRecord(value);
      const id = item && (typeof item.id === 'string' || typeof item.profile_id === 'string')
        ? String(item.id || item.profile_id)
        : '';
      return id ? { ...(item || {}), id } as Profile : null;
    })
    .filter((profile): profile is Profile => Boolean(profile));
}

export function profileLabel(profile: Profile): string {
  return profile.name || profile.display_name || profile.label || profile.id;
}

export async function listProfiles(): Promise<Profile[]> {
  return normalizeProfiles(await fetcher<unknown>('/profiles'));
}

export async function createProfile(payload: Record<string, unknown> = {}): Promise<Profile> {
  const response = await fetcher<unknown>('/profiles', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  const profiles = normalizeProfiles(response);
  const record = asRecord(response);
  const item = profiles[0] || (record?.profile && normalizeProfiles([record.profile])[0]);
  if (!item) throw new Error('A API não retornou um perfil criado.');
  return item;
}

export async function updateProfile(profileId: string, payload: Record<string, unknown>): Promise<Profile> {
  const response = await fetcher<unknown>(`/profiles/${encodeURIComponent(profileId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
  return normalizeProfiles(response)[0] || ({ id: profileId, ...payload } as Profile);
}

export async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  return fetcher<T>(url, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

// Centralized clients for the profile-scoped search workflow. Keeping these
// paths here makes it possible to evolve response envelopes without spreading
// endpoint knowledge across the UI.
export async function listSearchDefinitions(): Promise<unknown> {
  return fetcher('/search-definitions');
}

export async function createSearchDefinition(payload: unknown): Promise<unknown> {
  return sendJson('/search-definitions', 'POST', payload);
}

export async function updateSearchDefinition(id: string, payload: unknown): Promise<unknown> {
  return sendJson(`/search-definitions/${encodeURIComponent(id)}`, 'PATCH', payload);
}

export async function deleteSearchDefinition(id: string): Promise<void> {
  await sendJson(`/search-definitions/${encodeURIComponent(id)}`, 'DELETE');
}

export async function listSearchSessions(): Promise<unknown> {
  return fetcher('/search-sessions');
}

export async function getSearchSession(sessionId: string): Promise<unknown> {
  return fetcher(`/search-sessions/${encodeURIComponent(sessionId)}`);
}

export async function createSearchSession(payload: unknown): Promise<unknown> {
  return sendJson('/search-sessions', 'POST', payload);
}

export async function sendSearchMessage(sessionId: string, payload: unknown, reply = true): Promise<unknown> {
  const query = reply ? '?reply=true' : '';
  return sendJson(`/search-sessions/${encodeURIComponent(sessionId)}/messages${query}`, 'POST', payload);
}

export async function saveSearchDraft(sessionId: string, payload: unknown): Promise<unknown> {
  // PATCH is the canonical contract; callers can retry with PUT when talking
  // to an older compatible backend if needed.
  return sendJson(`/search-sessions/${encodeURIComponent(sessionId)}/draft`, 'PATCH', payload);
}

export async function confirmSearchSession(sessionId: string, payload?: unknown): Promise<unknown> {
  return sendJson(`/search-sessions/${encodeURIComponent(sessionId)}/confirm`, 'POST', payload);
}

export async function getSearchSessionResults(sessionId: string): Promise<unknown> {
  return fetcher(`/search-sessions/${encodeURIComponent(sessionId)}/results`);
}

export async function saveSearchSession(sessionId: string, payload?: unknown): Promise<unknown> {
  return sendJson(`/search-sessions/${encodeURIComponent(sessionId)}/save`, 'POST', payload);
}

export async function setOpportunityFeedback(opportunityId: string, value: string, reason?: unknown): Promise<unknown> {
  const positive = value === 'up' || value === 'like' || value === 'positive';
  return sendJson(`/opportunities/${encodeURIComponent(opportunityId)}/feedback`, 'PUT', {
    value,
    feedback: value,
    label: positive ? 'like' : 'dislike',
    rating: positive ? 1 : -1,
    ...(reason === undefined || reason === '' ? {} : { reason, reasons: Array.isArray(reason) ? reason : [reason], reason_codes: Array.isArray(reason) ? reason : [reason] }),
  });
}

export async function removeOpportunityFeedback(opportunityId: string): Promise<void> {
  await sendJson(`/opportunities/${encodeURIComponent(opportunityId)}/feedback`, 'DELETE');
}
