// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type { AgentEvent } from '@/lib/useAgentEvents';
import { CustomerDisplayPage } from './CustomerDisplayPage';

const mocks = vi.hoisted(() => ({
  onEvent: undefined as ((event: AgentEvent) => void) | undefined,
  payment: vi.fn().mockResolvedValue('pending'),
}));

vi.mock('@/lib/useAgentEvents', () => ({
  useAgentEvents: (_id: unknown, onEvent: (event: AgentEvent) => void) => {
    mocks.onEvent = onEvent;
  },
}));
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ language: 'en' }) }),
}));
vi.mock('@/lib/kioskPresentation', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/kioskPresentation')>(),
  fetchTouchPaymentStatus: mocks.payment,
}));

const menu = {
  view: 'menu', items: [], menu_items: [], display_mode: 'browse',
  result_complete: true, projected_count: 0, published_count: 0,
};
const payment = {
  view: 'payment_qr', order_id: 'coffee-order', payment_slug: 'coffee-payment',
  status: 'pending', qr_code: 'data:image/png;base64,Y29mZmVl',
  lines: [{ name: 'Black coffee', quantity: 1, unit_price: 35000, line_total: 35000 }],
  total: 35000, order_type: 'take-out', branch: '', created_at: '2026-10-15T02:15:00Z',
};

function emit(data: Record<string, unknown>) {
  act(() => mocks.onEvent?.({
    type: 'display_update', timestamp: 0,
    data: { presentation_session_id: 'customer-1', ...data },
  }));
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-15T02:16:00Z'));
  mocks.payment.mockClear();
  await act(async () => {
    render(<MemoryRouter initialEntries={['/customer-display?session=customer-1']}>
      <CustomerDisplayPage uiLanguage="en" />
    </MemoryRouter>);
  });
  emit(menu);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it.each(['touch', 'voice'])('retains the pending invoice after %s menu navigation', (navigation) => {
  emit(payment);
  if (navigation === 'touch') fireEvent.click(screen.getByTestId('customer-dock-tab-menu'));
  else emit(menu);
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));

  expect(screen.getByAltText('Verified payment QR code').getAttribute('src')).toBe(payment.qr_code);
  expect(screen.getByText(/INVOICE NO\.: coffee-order/)).toBeTruthy();
  expect(screen.getByText(/PAY WITHIN: 14:00/)).toBeTruthy();
  expect(screen.queryByTestId('cart-checkout')).toBeNull();
});

it('keeps the newest invoice and its original payment deadline', () => {
  emit(payment);
  emit({ ...payment, order_id: 'cake-order', qr_code: 'data:image/png;base64,Y2FrZQ==' });
  emit(menu);
  act(() => vi.advanceTimersByTime(60000));
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));

  expect(screen.getByText(/INVOICE NO\.: cake-order/)).toBeTruthy();
  expect(screen.getByText(/PAY WITHIN: 13:00/)).toBeTruthy();
  expect(screen.getByAltText('Verified payment QR code').getAttribute('src')).toBe('data:image/png;base64,Y2FrZQ==');
  expect(mocks.payment).toHaveBeenCalled();
});

it('uses a new draft once the customer adds another item, and forgets it on reset', () => {
  emit(payment);
  emit(menu);
  emit({
    view: 'cart', navigate: false, lines: [{ name: 'New cake', quantity: 1, line_total: 39000 }],
    total: 39000, order_type: '', table: '', table_name: '', order_note: '', pickup_minutes: 0,
  });
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.getByText('New cake')).toBeTruthy();
  expect(screen.queryByAltText('Verified payment QR code')).toBeNull();

  emit({ view: 'none' });
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.queryByText('New cake')).toBeNull();
  expect(screen.queryByText(/INVOICE NO\.: coffee-order/)).toBeNull();
});

it('returns to the paid invoice after browsing instead of restoring its old QR', () => {
  emit(payment);
  emit({ ...payment, view: 'bill', status: 'paid' });
  emit(menu);
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.getByText(/INVOICE NO\.: coffee-order/)).toBeTruthy();
  expect(screen.queryByAltText('Verified payment QR code')).toBeNull();
});

it('recovers the retained invoice from a menu replay after reconnect', () => {
  emit({ ...menu, cart_snapshot: payment });
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.getByAltText('Verified payment QR code').getAttribute('src')).toBe(payment.qr_code);
});

it('withdraws an invalid QR from the retained invoice without leaving the menu', () => {
  emit(payment);
  emit(menu);
  emit({ ...payment, view: 'bill', status: 'canceled', navigate: false });
  expect(screen.getByTestId('customer-dock').getAttribute('data-active-tab')).toBe('menu');
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.getByText(/STATUS: CANCELED/)).toBeTruthy();
  expect(screen.queryByAltText('Verified payment QR code')).toBeNull();
});

it('does not restore a QR after its original payment window expires', () => {
  emit(payment);
  emit(menu);
  act(() => vi.advanceTimersByTime(15 * 60000));
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.queryByAltText('Verified payment QR code')).toBeNull();
  expect(screen.getByText(/PAYMENT TIME EXPIRED/)).toBeTruthy();
});

it('keeps a newer draft when an older receipt finishes printing', () => {
  emit(payment);
  fireEvent.click(screen.getByTestId('customer-dock-tab-menu'));
  emit({
    view: 'cart', navigate: false, lines: [{ name: 'New cake', quantity: 1, line_total: 39000 }],
    total: 39000, order_type: '', table: '', table_name: '', order_note: '', pickup_minutes: 0,
  });
  emit({ view: 'print_status', order_id: 'coffee-order', printing_state: 'completed' });
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.getByText('New cake')).toBeTruthy();
  expect(screen.queryByAltText('Verified payment QR code')).toBeNull();
});

it('forgets the previous customer invoice when reconnect replay reports a reset', () => {
  emit(payment);
  emit({ ...menu, cart_snapshot: null });
  fireEvent.click(screen.getByTestId('customer-dock-tab-cart'));
  expect(screen.queryByAltText('Verified payment QR code')).toBeNull();
  expect(screen.queryByText(/INVOICE NO\.: coffee-order/)).toBeNull();
});
