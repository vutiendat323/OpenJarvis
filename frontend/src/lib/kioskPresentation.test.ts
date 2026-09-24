import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  checkoutTouchCart,
  createKioskPresentationLifecycle,
  editTouchCart,
  fetchTouchPaymentStatus,
  fetchTouchTables,
  shareScreenSearch,
  ensurePresentationSession,
  shouldEnsurePresentationSession,
  resetPresentationSession,
} from './kioskPresentation';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('kiosk presentation API', () => {
  it('re-ensures presentation while a customer is approaching or being served', () => {
    expect(shouldEnsurePresentationSession('idle')).toBe(false);
    expect(shouldEnsurePresentationSession('cleanup')).toBe(false);
    expect(shouldEnsurePresentationSession('approaching')).toBe(true);
    expect(shouldEnsurePresentationSession('prompting')).toBe(true);
    expect(shouldEnsurePresentationSession('active')).toBe(true);
  });

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

  it('correlates a reset with the active voice generation', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);

    await resetPresentationSession('session-1', 'thread-1');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/kiosk/presentation/session-1/reset',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ generation: 'thread-1' }),
      },
    );
  });

  it('raises concise errors for non-OK responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));

    await expect(ensurePresentationSession('http://127.0.0.1:5173'))
      .rejects.toThrow('Unable to start customer display');
    await expect(resetPresentationSession('session-1'))
      .rejects.toThrow('Unable to reset customer display');
  });

  it('retries a transient presentation failure until the display session is ready', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 503 })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ presentation_session_id: 'recovered-session' }),
      });
    vi.stubGlobal('fetch', fetchMock);

    const session = ensurePresentationSession('http://127.0.0.1:5173');
    const recovered = expect(session).resolves.toBe('recovered-session');
    await vi.advanceTimersByTimeAsync(500);

    await recovered;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('kiosk presentation lifecycle', () => {
  it('does not reset a preloaded display before the first voice turn', () => {
    const reset = vi.fn().mockResolvedValue(undefined);
    const lifecycle = createKioskPresentationLifecycle(reset);

    lifecycle.requestReset();
    lifecycle.setSessionId('session-1');
    expect(reset).not.toHaveBeenCalled();

    lifecycle.markActive('thread-1');
    lifecycle.requestReset();
    expect(reset).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenLastCalledWith('session-1', 'thread-1');
  });

  it('waits for voice teardown before requesting presentation reset', async () => {
    let finishVoiceEnd: (() => void) | undefined;
    const endVoice = vi.fn(() => new Promise<void>((resolve) => {
      finishVoiceEnd = resolve;
    }));
    const reset = vi.fn().mockResolvedValue(undefined);
    const lifecycle = createKioskPresentationLifecycle(reset);
    lifecycle.setSessionId('session-1');
    lifecycle.markActive('thread-1');

    const teardown = lifecycle.endVoiceThenReset(endVoice);
    const duplicateTeardown = lifecycle.endVoiceThenReset(endVoice);
    expect(endVoice).toHaveBeenCalledOnce();
    expect(reset).not.toHaveBeenCalled();

    finishVoiceEnd?.();
    await Promise.all([teardown, duplicateTeardown]);
    expect(reset).toHaveBeenCalledOnce();
  });

  it('drops a pre-ensure reset when a new voice generation becomes active', () => {
    const reset = vi.fn().mockResolvedValue(undefined);
    const lifecycle = createKioskPresentationLifecycle(reset);

    lifecycle.requestReset();
    lifecycle.markActive('thread-1');
    lifecycle.setSessionId('session-1');

    expect(reset).not.toHaveBeenCalled();
  });
});

describe('touch cart API', () => {
  it('adds a tapped portion and returns the verified draft cart', async () => {
    const line = { line_id: 'l1', name: 'Latte', size: 'lớn', note: '', quantity: 2, unit_price: 55000, line_total: 110000 };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        cart: {
          revision: 3,
          lines: [{ ...line, variant_id: 'v-latte-l' }],
          total: 110000,
          order_note: '',
          order_type: '',
          table: '',
          table_name: '',
          pickup_minutes: 0,
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const edit = { action: 'add', variant_id: 'v-latte-l', quantity: 2, note: '' } as const;

    await expect(editTouchCart('customer/1', edit))
      .resolves.toEqual({
        view: 'cart',
        lines: [line],
        total: 110000,
        order_note: '',
        order_type: '',
        table: '',
        table_name: '',
        pickup_minutes: 0,
      });
    expect(fetchMock).toHaveBeenCalledWith('/api/kiosk/presentation/customer%2F1/cart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(edit),
    });
  });

  it('surfaces the reason the server refused the order', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'voice_session_required' }),
    }));

    await expect(editTouchCart('s', { action: 'clear' }))
      .rejects.toThrow('voice_session_required');
  });

  it('lists only well-formed live tables', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        tables: [
          { slug: 't1', name: '1', status: 'available' },
          { slug: 't2', name: '2', status: 'reserved' },
          { slug: 7, name: 'broken', status: 'available' },
        ],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchTouchTables('s/1')).resolves.toEqual([
      { slug: 't1', name: '1', status: 'available' },
      { slug: 't2', name: '2', status: 'reserved' },
    ]);
    expect(fetchMock).toHaveBeenCalledWith('/api/kiosk/presentation/s%2F1/tables', { headers: {} });
  });

  it('places the saved draft and surfaces a refusal', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) })
      .mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ detail: 'table_required' }) });
    vi.stubGlobal('fetch', fetchMock);

    await expect(checkoutTouchCart('s')).resolves.toBeUndefined();
    await expect(checkoutTouchCart('s')).rejects.toThrow('table_required');
    expect(fetchMock).toHaveBeenCalledWith('/api/kiosk/presentation/s/checkout', { method: 'POST', headers: {} });
  });

  it('reads the payment status of the order on screen', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'paid' }) }));

    await expect(fetchTouchPaymentStatus('s')).resolves.toBe('paid');
  });
});

describe('screen search sharing', () => {
  it('sends only the ids of the items the search shows, in screen order', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: 2 }) });
    vi.stubGlobal('fetch', fetchMock);

    await shareScreenSearch('s/1', ['v-3', 'v-2']);

    expect(fetchMock).toHaveBeenCalledWith('/api/kiosk/presentation/s%2F1/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_ids: ['v-3', 'v-2'] }),
    });
  });
});

