import { describe, expect, it } from 'vitest';

import type { AgentEvent } from '@/lib/useAgentEvents';
import {
  backgroundCart,
  isSafeQrImageSource,
  paymentDeadline,
  menuItemDetails,
  menuItemPortions,
  reduceCustomerDisplay,
  searchMenuItems,
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

function verifiedMenu(
  items: Record<string, unknown>[],
  menuItems: Record<string, unknown>[] = items,
  displayMode: 'browse' | 'filtered' = 'filtered',
) {
  return {
    view: 'menu',
    items,
    menu_items: menuItems,
    display_mode: displayMode,
    result_complete: true,
    projected_count: items.length,
    published_count: items.length,
  };
}

describe('reduceCustomerDisplay', () => {
  it('accepts a matching menu event and ignores another session', () => {
    const state = reduceCustomerDisplay(waitingState, displayEvent('A', verifiedMenu([
      { id: 'latte', name: 'Latte', price: 45000 },
    ])), 'A');

    expect(state).toEqual({
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
      menuItems: [{ id: 'latte', name: 'Latte', price: 45000 }],
      displayMode: 'filtered',
      resultComplete: true,
      projectedCount: 1,
      publishedCount: 1,
      preview: false,
    });
    expect(reduceCustomerDisplay(
      state,
      displayEvent('B', { view: 'none' }),
      'A',
    )).toBe(state);
  });

  it.each([0, 1, 6, 7, 100])('accepts all %i verified menu items', (count) => {
    const items = Array.from({ length: count }, (_, index) => ({
      id: `item-${index}`,
      name: `Item ${index}`,
    }));

    expect(reduceCustomerDisplay(
      waitingState,
      displayEvent('A', verifiedMenu(items)),
      'A',
    )).toEqual({
      view: 'menu',
      items,
      menuItems: items,
      displayMode: 'filtered',
      resultComplete: true,
      projectedCount: count,
      publishedCount: count,
      preview: false,
    });
  });

  it('keeps the complete live catalog separate from filtered results', () => {
    const catalog = [
      {
        id: 'trend-coffee',
        name: 'Coffee Trend',
        price: 60000,
        category: 'cà phê',
        is_top_sell: true,
        is_new: false,
      },
      {
        id: 'matcha',
        name: 'Matcha Trend',
        price: 60000,
        category: 'món trà',
        is_top_sell: false,
        is_new: true,
      },
    ];

    expect(reduceCustomerDisplay(
      waitingState,
      displayEvent('A', verifiedMenu([catalog[1]], catalog, 'browse')),
      'A',
    )).toMatchObject({
      view: 'menu',
      items: [catalog[1]],
      menuItems: catalog,
      displayMode: 'browse',
    });
  });

  it('retains prior state for an invalid menu display mode', () => {
    const data = verifiedMenu([{ id: 'one', name: 'One' }]);

    expect(reduceCustomerDisplay(
      waitingState,
      displayEvent('A', { ...data, display_mode: 'invented' }),
      'A',
    )).toBe(waitingState);
  });

  it('retains previous state when complete menu counts do not match', () => {
    const prior = waitingState;
    const data = verifiedMenu([{ id: 'one', name: 'One' }]);

    for (const mismatch of [
      { ...data, projected_count: 2 },
      { ...data, published_count: 0 },
      { ...data, result_complete: false },
    ]) {
      expect(reduceCustomerDisplay(prior, displayEvent('A', mismatch), 'A')).toBe(prior);
    }
  });

  it('returns waiting state on a matching reset event', () => {
    const menuState: CustomerDisplayState = {
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
      menuItems: [{ id: 'latte', name: 'Latte', price: 45000 }],
      displayMode: 'filtered',
      resultComplete: true,
      projectedCount: 1,
      publishedCount: 1,
      preview: false,
    };

    expect(reduceCustomerDisplay(
      menuState,
      displayEvent('A', { view: 'none' }),
      'A',
    )).toEqual(waitingState);
  });

  it('ignores malformed payloads and drops fields the display does not render', () => {
    const state = reduceCustomerDisplay(waitingState, displayEvent('A', verifiedMenu([
      { id: 'latte', price: 45000 },
    ])), 'A');
    expect(state).toBe(waitingState);

    expect(reduceCustomerDisplay(waitingState, displayEvent('A', verifiedMenu([
      { id: 'latte', name: 'Latte', price: 45000, html: '<script>x</script>' },
    ])), 'A')).toEqual({
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
      menuItems: [{ id: 'latte', name: 'Latte', price: 45000 }],
      displayMode: 'filtered',
      resultComplete: true,
      projectedCount: 1,
      publishedCount: 1,
      preview: false,
    });
  });

  it('ignores null and non-record event data without throwing', () => {
    const state: CustomerDisplayState = {
      view: 'menu',
      items: [{ id: 'latte', name: 'Latte', price: 45000 }],
      menuItems: [{ id: 'latte', name: 'Latte', price: 45000 }],
      displayMode: 'filtered',
      resultComplete: true,
      projectedCount: 1,
      publishedCount: 1,
      preview: false,
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

  it('keeps normalized draft identity, prices, notes, and order type', () => {
    expect(reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'cart',
      lines: [{
        line_id: 'line-1',
        name: 'Cà phê sữa',
        size: 'tiêu chuẩn',
        note: 'ít đá',
        quantity: 3,
        unit_price: 40000,
        line_total: 120000,
        html: '<script>x</script>',
      }],
      total: 120000,
      order_note: 'Làm nhanh giúp mình',
      order_type: 'at-table',
      table: 'table-73',
      table_name: '73',
      pickup_minutes: 0,
      provider_status: 'invented',
    }), 'A')).toEqual({
      view: 'cart',
      lines: [{
        line_id: 'line-1',
        name: 'Cà phê sữa',
        size: 'tiêu chuẩn',
        note: 'ít đá',
        quantity: 3,
        unit_price: 40000,
        line_total: 120000,
      }],
      total: 120000,
      order_note: 'Làm nhanh giúp mình',
      order_type: 'at-table',
      table: 'table-73',
      table_name: '73',
      pickup_minutes: 0,
    });
  });

  it('keeps the take-out pickup time and defaults it for older carts', () => {
    const cart = (extra: Record<string, unknown>) => reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'cart', lines: [], total: 0, order_type: 'take-out', ...extra,
    }), 'A');

    expect(cart({ pickup_minutes: 15 })).toMatchObject({ view: 'cart', pickup_minutes: 15 });
    expect(cart({})).toMatchObject({ view: 'cart', pickup_minutes: 0 });
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

  it('accepts a self-contained normalized payment receipt snapshot', () => {
    expect(reduceCustomerDisplay(waitingState, displayEvent('A', {
      view: 'payment_qr',
      order_id: 'order-1',
      payment_slug: 'payment-1',
      status: 'pending',
      qr_code: 'merchant-opaque-qr',
      order_type: 'at-table',
      branch: 'br-thu-duc',
      table_name: '73',
      lines: [{
        name: 'Cà phê đen',
        quantity: 2,
        unit_price: 35000,
        line_total: 70000,
        html: '<script>x</script>',
      }],
      total: 45000,
      created_at: 'Thu Sep 17 2026 19:00:00 GMT+0700 (Indochina Time)',
      html: '<img src=x onerror=alert(1)>',
      receipt_id: 'receipt-that-must-not-be-shown',
    }), 'A')).toEqual({
      view: 'payment_qr',
      order_id: 'order-1',
      payment_slug: 'payment-1',
      status: 'pending',
      qr_code: 'merchant-opaque-qr',
      order_type: 'at-table',
      branch: 'br-thu-duc',
      table_name: '73',
      lines: [{
        name: 'Cà phê đen',
        quantity: 2,
        unit_price: 35000,
        line_total: 70000,
      }],
      total: 45000,
      created_at: 'Thu Sep 17 2026 19:00:00 GMT+0700 (Indochina Time)',
    });
  });

  it('does not inherit receipt data from the previously displayed view', () => {
    const priorBill: CustomerDisplayState = {
      view: 'bill',
      order_id: 'order-1',
      status: 'placed',
      order_type: 'take-out',
      branch: 'br-thu-duc',
      lines: [{ name: 'Cà phê đen', quantity: 1, line_total: 40000 }],
      total: 40000,
    };

    expect(reduceCustomerDisplay(priorBill, displayEvent('A', {
      view: 'payment_qr',
      order_id: 'order-1',
      payment_slug: 'payment-1',
      status: 'pending',
      qr_code: 'merchant-opaque-qr',
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

describe('menu item portions', () => {
  it('keeps only well-formed live portions from a menu event', () => {
    const state = reduceCustomerDisplay(waitingState, displayEvent('A', verifiedMenu([
      {
        id: 'latte',
        name: 'Latte',
        price: 45000,
        variants: [
          { id: 'latte', size: 'nhỏ', price: 45000 },
          { id: 'latte-l', size: 'lớn', price: '55000' },
          { id: '', size: 'vừa', price: 50000 },
          'latte-xl',
        ],
      },
    ])), 'A');

    expect(state.view === 'menu' && state.menuItems[0].variants).toEqual([
      { id: 'latte', size: 'nhỏ', price: 45000 },
    ]);
  });

  it('falls back to the item identity when the menu carried no portions', () => {
    expect(menuItemPortions({ id: 'tea', name: 'Trà', price: 30000 })).toEqual([
      { id: 'tea', size: '', price: 30000 },
    ]);
    expect(menuItemPortions({ name: 'Unpriced' })).toEqual([]);
  });

  it('opens the complete catalog row for a tapped recommendation', () => {
    const catalog = [
      { id: 'latte', name: 'Latte', price: 45000, image_url: 'https://x/latte.jpg' },
    ];

    expect(menuItemDetails({ id: 'latte', name: 'Latte' }, catalog)).toBe(catalog[0]);
    expect(menuItemDetails({ id: 'gone', name: 'Gone' }, catalog)).toEqual({
      id: 'gone',
      name: 'Gone',
    });
  });
});

describe('paymentDeadline', () => {
  it('gives the merchant fifteen minutes from the order creation time', () => {
    const created = 'Thu Sep 17 2026 19:00:00 GMT+0700 (Indochina Time)';

    expect(paymentDeadline(created, 0)).toBe(Date.parse(created) + 15 * 60_000);
  });

  it('starts the window when the QR first appears if the time is unreadable', () => {
    expect(paymentDeadline(undefined, 1_000)).toBe(1_000 + 15 * 60_000);
    expect(paymentDeadline('not a date', 1_000)).toBe(1_000 + 15 * 60_000);
  });
});

describe('searchMenuItems', () => {
  const catalog = [
    { id: 'cafe', name: 'Cà phê sữa', category: 'cà phê', note: 'Nóng / Đá.' },
    { id: 'tea', name: 'Trà đào cam sả', category: 'món trà', note: '' },
    { id: 'beer', name: 'Corona Extra 4.5%', category: 'bia/ rượu vang ' },
    { id: 'rolls', name: 'Cinnamon rolls', category: 'món bánh', note: 'Bánh quế đường đen' },
  ];
  const ids = (query: string) => searchMenuItems(catalog, query).map((item) => item.id);

  it('matches names ignoring case and Vietnamese accents', () => {
    expect(ids('CA PHE')).toEqual(['cafe']);
    expect(ids('trà ĐÀO')).toEqual(['tea']);
  });

  it('matches while a word is still being typed', () => {
    expect(ids('cinn')).toEqual(['rolls']);
    expect(ids('ca ph')).toEqual(['cafe']);
  });

  it('matches categories and descriptions as well as names', () => {
    expect(ids('bia')).toEqual(['beer']);
    expect(ids('duong den')).toEqual(['rolls']);
  });

  it('keeps live catalog order and never matches inside a word', () => {
    expect(ids('tra')).toEqual(['tea']);
    expect(ids('mon')).toEqual(['tea', 'rolls']);
  });

  it('returns nothing for a query with no searchable words', () => {
    expect(ids('!!!')).toEqual([]);
    expect(ids('matcha')).toEqual([]);
  });
});

describe('background cart updates', () => {
  const line = { line_id: 'l1', name: 'Latte', quantity: 2, unit_price: 45000, line_total: 90000 };
  const cartData = { view: 'cart', lines: [line], total: 90000, order_type: '' };
  const menu = reduceCustomerDisplay(waitingState, displayEvent('A', verifiedMenu([
    { id: 'latte', name: 'Latte', price: 45000 },
  ])), 'A');

  it('keeps the customer on the menu while the cart is saved', () => {
    const event = displayEvent('A', { ...cartData, navigate: false });

    expect(reduceCustomerDisplay(menu, event, 'A')).toBe(menu);
    expect(backgroundCart(event, 'A')).toMatchObject({ view: 'cart', lines: [line], total: 90000 });
  });

  it('refreshes a cart receipt that is already on screen', () => {
    const open = reduceCustomerDisplay(waitingState, displayEvent('A', { ...cartData, total: 45000 }), 'A');
    const event = displayEvent('A', { ...cartData, navigate: false });

    expect(reduceCustomerDisplay(open, event, 'A')).toMatchObject({ view: 'cart', total: 90000 });
  });

  it('treats only this session\'s silent cart saves as background updates', () => {
    expect(backgroundCart(displayEvent('A', cartData), 'A')).toBeNull();
    expect(backgroundCart(displayEvent('B', { ...cartData, navigate: false }), 'A')).toBeNull();
    expect(backgroundCart(displayEvent('A', { view: 'menu', navigate: false }), 'A')).toBeNull();
  });
});

