import { describe, expect, it } from 'vitest';

import type { AgentEvent } from '@/lib/useAgentEvents';
import {
  isSafeQrImageSource,
  reduceCustomerDisplay,
  waitingState,
  type CustomerDisplayState,
} from './customerDisplayState';

function displayEvent(
  sessionId: string,
  data: Record<string, unknown>,
): AgentEvent {
  return {
    type: 'display_update',
    timestamp: 0,
    data: { ...data, presentation_session_id: sessionId },
  };
}

describe('reduceCustomerDisplay', () => {
  it('accepts a matching menu event and ignores another session', () => {
    const state = reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
    }), 'A');

    expect(state).toEqual({
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
    });
    expect(reduceCustomerDisplay(
      state,
      displayEvent('B', { view: 'none' }),
      'A',
    )).toBe(state);
  });

  it('returns waiting state on a matching reset event', () => {
    const menuState: CustomerDisplayState = {
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
    };

    expect(reduceCustomerDisplay(
      menuState,
      displayEvent('A', { view: 'none' }),
      'A',
    )).toEqual(waitingState);
  });

  it('ignores malformed payloads and drops fields the display does not render', () => {
    const state = reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'menu',
      items: [{ id: 'latte', price: 45000 }],
    }), 'A');
    expect(state).toBe(waitingState);

    expect(reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000, html: '<script>x</script>' }],
    }), 'A')).toEqual({
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
    });
  });
});

describe('isSafeQrImageSource', () => {
  it('allows only HTTPS and inline image values to load as images', () => {
    expect(isSafeQrImageSource('https://payments.example/qr.png')).toBe(true);
    expect(isSafeQrImageSource('data:image/png;base64,abc')).toBe(true);
    expect(isSafeQrImageSource('http://payments.example/qr.png')).toBe(false);
    expect(isSafeQrImageSource('javascript:alert(1)')).toBe(false);
    expect(isSafeQrImageSource('merchant-opaque-qr')).toBe(false);
  });
});
