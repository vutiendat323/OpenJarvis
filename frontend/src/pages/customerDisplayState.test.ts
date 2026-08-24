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

  it('ignores null and non-record event data without throwing', () => {
    const state: CustomerDisplayState = {
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
    };

    for (const data of [null, 'not-an-object']) {
      const event = {
        type: 'display_update',
        timestamp: 0,
        data,
      } as unknown as AgentEvent;
      expect(reduceCustomerDisplay(state, event, 'A')).toBe(state);
    }
  });

  it('accepts a normalized bill and drops invented line fields', () => {
    expect(reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'bill',
      order_id: 'order-1',
      status: 'placed',
      order_type: 'take-out',
      branch: 'br-thu-duc',
      lines: [{
        name: 'Cà phê đen',
        quantity: 2,
        line_total: 70000,
        html: '<script>x</script>',
      }],
      total: 70000,
      receipt_id: 'invented-receipt',
    }), 'A')).toEqual({
      view: 'bill',
      order_id: 'order-1',
      status: 'placed',
      order_type: 'take-out',
      branch: 'br-thu-duc',
      lines: [{ name: 'Cà phê đen', quantity: 2, line_total: 70000 }],
      total: 70000,
    });
  });

  it('accepts only the normalized payment QR fields', () => {
    expect(reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'payment_qr',
      order_id: 'order-1',
      payment_slug: 'payment-1',
      status: 'pending',
      qr_code: 'merchant-opaque-qr',
      html: '<img src=x onerror=alert(1)>',
      receipt_id: 'receipt-that-must-not-be-shown',
    }), 'A')).toEqual({
      view: 'payment_qr',
      order_id: 'order-1',
      payment_slug: 'payment-1',
      status: 'pending',
      qr_code: 'merchant-opaque-qr',
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
