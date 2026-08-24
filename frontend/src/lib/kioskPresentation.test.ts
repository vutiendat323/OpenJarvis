import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ensurePresentationSession,
  resetPresentationSession,
} from './kioskPresentation';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('kiosk presentation API', () => {
  it('ensures a presentation session for the current frontend origin', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ presentation_session_id: 'session-1' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(ensurePresentationSession(window.location.origin))
      .resolves.toBe('session-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/kiosk/presentation/ensure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_origin: window.location.origin }),
    });
  });

  it('resets the encoded presentation session path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);

    await resetPresentationSession('customer/session 1');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/kiosk/presentation/customer%2Fsession%201/reset',
      { method: 'POST', headers: {} },
    );
  });

  it('raises concise errors for non-OK responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));

    await expect(ensurePresentationSession('http://127.0.0.1:5173'))
      .rejects.toThrow('Unable to start customer display');
    await expect(resetPresentationSession('session-1'))
      .rejects.toThrow('Unable to reset customer display');
  });
});
