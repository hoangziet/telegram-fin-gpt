export type HealthResponse =
  { status: 'alive' | 'ready' } | { status: 'not_ready'; reason: string };

export interface AuthUser {
  id: string;
  username: string;
}

export interface LoginResult {
  user: AuthUser;
  csrfToken: string;
}

export class ApiError extends Error {
  readonly statusCode: number;

  constructor(message: string, statusCode: number) {
    super(message);
    this.name = 'ApiError';
    this.statusCode = statusCode;
  }
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health/live', {
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
  });
  const payload: unknown = await response.json();

  if (!response.ok) {
    throw new ApiError('Không thể kết nối Where my bucks go?.', response.status);
  }
  if (!isHealthResponse(payload)) {
    throw new ApiError('Máy chủ trả về dữ liệu không hợp lệ.', response.status);
  }
  return payload;
}

export async function getCurrentUser(): Promise<AuthUser> {
  const payload = await requestJson('/api/auth/me');
  if (!isRecord(payload) || !isAuthUser(payload.user)) {
    throw new ApiError('Máy chủ trả về dữ liệu không hợp lệ.', 502);
  }
  return payload.user;
}

export async function getCsrfToken(): Promise<string> {
  const payload = await requestJson('/api/auth/csrf');
  if (!isRecord(payload) || typeof payload.csrfToken !== 'string') {
    throw new ApiError('Máy chủ trả về dữ liệu không hợp lệ.', 502);
  }
  return payload.csrfToken;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const payload = await requestJson('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!isRecord(payload) || !isAuthUser(payload.user) || typeof payload.csrfToken !== 'string') {
    throw new ApiError('Máy chủ trả về dữ liệu không hợp lệ.', 502);
  }
  return { user: payload.user, csrfToken: payload.csrfToken };
}

export async function logout(csrfToken: string): Promise<void> {
  const response = await fetch('/api/auth/logout', {
    method: 'POST',
    headers: { Accept: 'application/json', 'X-CSRF-Token': csrfToken },
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (!response.ok) throw await toApiError(response);
}

async function requestJson(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: 'application/json', ...init.headers },
    credentials: 'same-origin',
    cache: 'no-store',
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw toApiErrorFromPayload(response.status, payload);
  return payload;
}

async function toApiError(response: Response): Promise<ApiError> {
  const payload: unknown = await response.json().catch(() => null);
  return toApiErrorFromPayload(response.status, payload);
}

function toApiErrorFromPayload(statusCode: number, payload: unknown): ApiError {
  const message =
    isRecord(payload) && isRecord(payload.error) && typeof payload.error.message === 'string'
      ? payload.error.message
      : 'Yêu cầu không thể hoàn tất.';
  return new ApiError(message, statusCode);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isAuthUser(value: unknown): value is AuthUser {
  return isRecord(value) && typeof value.id === 'string' && typeof value.username === 'string';
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null || !('status' in value)) {
    return false;
  }
  return (
    value.status === 'alive' ||
    value.status === 'ready' ||
    (value.status === 'not_ready' && 'reason' in value && typeof value.reason === 'string')
  );
}
