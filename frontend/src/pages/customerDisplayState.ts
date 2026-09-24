import type { AgentEvent } from '@/lib/useAgentEvents';

export interface CustomerMenuVariant {
  id: string;
  size: string;
  price: number;
}

export interface CustomerMenuItem {
  id?: string;
  name: string;
  price?: number;
  available?: boolean;
  note?: string;
  image_url?: string;
  category?: string;
  is_top_sell?: boolean;
  is_new?: boolean;
  variants?: CustomerMenuVariant[];
}

export type CustomerMenuDisplayMode = 'browse' | 'filtered';

export interface CustomerDisplayLine {
  line_id?: string;
  name: string;
  size?: string;
  note?: string;
  quantity?: number;
  unit_price?: number;
  line_total?: number;
}

export type CustomerDisplayState =
  | { view: 'waiting' }
  | {
      view: 'menu';
      items: CustomerMenuItem[];
      menuItems: CustomerMenuItem[];
      displayMode: CustomerMenuDisplayMode;
      resultComplete: boolean;
      projectedCount: number;
      publishedCount: number;
      preview: boolean;
    }
  | {
      view: 'cart';
      lines: CustomerDisplayLine[];
      total: number;
      order_note: string;
      order_type: string;
      table: string;
      table_name: string;
      pickup_minutes: number;
    }
  | {
      view: 'bill';
      order_id: string;
      status: string;
      order_type: string;
      branch: string;
      lines: CustomerDisplayLine[];
      total: number;
    }
  | {
      view: 'payment_qr';
      order_id: string;
      payment_slug: string;
      status: string;
      qr_code: string;
      order_type?: string;
      branch?: string;
      table_name?: string;
      lines?: CustomerDisplayLine[];
      total?: number;
      created_at?: string;
    };

export const waitingState: CustomerDisplayState = { view: 'waiting' };

/** The merchant cancels an unpaid transfer fifteen minutes after ordering. */
export const PAYMENT_WINDOW_MS = 15 * 60_000;

/** When the payment QR stops being payable; `shownAt` covers an unreadable time. */
export function paymentDeadline(createdAt: string | undefined, shownAt: number): number {
  const created = createdAt ? Date.parse(createdAt) : Number.NaN;
  return (Number.isFinite(created) ? created : shownAt) + PAYMENT_WINDOW_MS;
}

export function isSafeQrImageSource(value: string): boolean {
  if (value.startsWith('data:image/')) return true;
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function optionalString(row: Record<string, unknown>, field: string): string | undefined {
  return typeof row[field] === 'string' ? row[field] : undefined;
}

function optionalNumber(row: Record<string, unknown>, field: string): number | undefined {
  const value = row[field];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function optionalBoolean(row: Record<string, unknown>, field: string): boolean | undefined {
  return typeof row[field] === 'boolean' ? row[field] : undefined;
}

function pickVariants(value: unknown): CustomerMenuVariant[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap((row) => (
    isRecord(row)
    && typeof row.id === 'string'
    && row.id.trim()
    && optionalNumber(row, 'price') !== undefined
      ? [{ id: row.id, size: optionalString(row, 'size') ?? '', price: row.price as number }]
      : []
  ));
}

function pickMenuItem(value: unknown): CustomerMenuItem | null {
  if (!isRecord(value) || typeof value.name !== 'string') return null;
  const variants = pickVariants(value.variants);
  return {
    ...(optionalString(value, 'id') !== undefined ? { id: optionalString(value, 'id') } : {}),
    name: value.name,
    ...(optionalNumber(value, 'price') !== undefined ? { price: optionalNumber(value, 'price') } : {}),
    ...(optionalBoolean(value, 'available') !== undefined ? { available: optionalBoolean(value, 'available') } : {}),
    ...(optionalString(value, 'note') !== undefined ? { note: optionalString(value, 'note') } : {}),
    ...(optionalString(value, 'image_url') !== undefined ? { image_url: optionalString(value, 'image_url') } : {}),
    ...(optionalString(value, 'category') !== undefined ? { category: optionalString(value, 'category') } : {}),
    ...(optionalBoolean(value, 'is_top_sell') !== undefined ? { is_top_sell: optionalBoolean(value, 'is_top_sell') } : {}),
    ...(optionalBoolean(value, 'is_new') !== undefined ? { is_new: optionalBoolean(value, 'is_new') } : {}),
    ...(variants !== undefined ? { variants } : {}),
  };
}

/** The orderable portions of a menu item, as the live menu published them. */
export function menuItemPortions(item: CustomerMenuItem): CustomerMenuVariant[] {
  if (item.variants?.length) return item.variants;
  return item.id && item.price !== undefined ? [{ id: item.id, size: '', price: item.price }] : [];
}

/** Recommendations are slim rows; the catalog row carries photo and portions. */
export function menuItemDetails(item: CustomerMenuItem, catalog: CustomerMenuItem[]): CustomerMenuItem {
  return catalog.find((row) => item.id !== undefined && row.id === item.id) ?? item;
}

/** Lowercased words without Vietnamese accents, so "Cà Phê" and "ca phe" meet. */
function searchWords(text: string): string[] {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/đ/g, 'd')
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

/**
 * Items whose name, category or description has a word starting with every
 * typed word, in live catalog order. Prefixes keep results useful mid-typing.
 */
export function searchMenuItems(items: CustomerMenuItem[], query: string): CustomerMenuItem[] {
  const terms = searchWords(query);
  if (terms.length === 0) return [];
  return items.filter((item) => {
    const words = searchWords([item.name, item.category ?? '', item.note ?? ''].join(' '));
    return terms.every((term) => words.some((word) => word.startsWith(term)));
  });
}

function pickLine(value: unknown): CustomerDisplayLine | null {
  if (!isRecord(value) || typeof value.name !== 'string') return null;
  return {
    ...(optionalString(value, 'line_id') !== undefined ? { line_id: optionalString(value, 'line_id') } : {}),
    name: value.name,
    ...(optionalString(value, 'size') !== undefined ? { size: optionalString(value, 'size') } : {}),
    ...(optionalString(value, 'note') !== undefined ? { note: optionalString(value, 'note') } : {}),
    ...(optionalNumber(value, 'quantity') !== undefined ? { quantity: optionalNumber(value, 'quantity') } : {}),
    ...(optionalNumber(value, 'unit_price') !== undefined ? { unit_price: optionalNumber(value, 'unit_price') } : {}),
    ...(optionalNumber(value, 'line_total') !== undefined ? { line_total: optionalNumber(value, 'line_total') } : {}),
  };
}

function pickRows<T>(value: unknown, pick: (row: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) return null;
  const rows: T[] = [];
  for (const valueRow of value) {
    const row = pick(valueRow);
    if (row === null) return null;
    rows.push(row);
  }
  return rows;
}

function matchingData(event: AgentEvent, sessionId: string): Record<string, unknown> | null {
  if (
    event.type !== 'display_update'
    || !isRecord(event.data)
    || event.data.presentation_session_id !== sessionId
  ) {
    return null;
  }
  return event.data;
}

type CartState = Extract<CustomerDisplayState, { view: 'cart' }>;

function pickCart(data: Record<string, unknown>): CartState | null {
  const lines = pickRows(data.lines, pickLine);
  const total = optionalNumber(data, 'total');
  if (lines === null || total === undefined) return null;
  return {
    view: 'cart',
    lines,
    total,
    order_note: optionalString(data, 'order_note') ?? '',
    order_type: optionalString(data, 'order_type') ?? '',
    table: optionalString(data, 'table') ?? '',
    table_name: optionalString(data, 'table_name') ?? '',
    pickup_minutes: optionalNumber(data, 'pickup_minutes') ?? 0,
  };
}

/** A cart saved without navigating ("add to cart" while browsing), or null. */
export function backgroundCart(event: AgentEvent, sessionId: string): CartState | null {
  const data = matchingData(event, sessionId);
  if (!data || data.view !== 'cart' || data.navigate !== false) return null;
  return pickCart(data);
}

export function reduceCustomerDisplay(
  state: CustomerDisplayState,
  event: AgentEvent,
  sessionId: string,
): CustomerDisplayState {
  const data = matchingData(event, sessionId);
  if (!data || typeof data.view !== 'string') return state;
  if (data.view === 'none') return waitingState;

  if (data.view === 'menu') {
    const items = pickRows(data.items, pickMenuItem);
    const menuItems = pickRows(data.menu_items, pickMenuItem);
    const displayMode = data.display_mode;
    const projectedCount = optionalNumber(data, 'projected_count');
    const publishedCount = optionalNumber(data, 'published_count');
    return (
      items === null
      || menuItems === null
      || (displayMode !== 'browse' && displayMode !== 'filtered')
      || data.result_complete !== true
      || projectedCount !== items.length
      || publishedCount !== items.length
    )
      ? state
      : {
          view: 'menu',
          items,
          menuItems,
          displayMode,
          resultComplete: true,
          projectedCount,
          publishedCount,
          preview: false,
        };
  }

  if (data.view === 'cart') {
    const cart = pickCart(data);
    // A background save updates a receipt already on screen, never the screen.
    if (cart === null || (data.navigate === false && state.view !== 'cart')) return state;
    return cart;
  }

  if (data.view === 'bill') {
    const lines = pickRows(data.lines, pickLine);
    const total = optionalNumber(data, 'total');
    if (
      lines === null
      || total === undefined
      || typeof data.order_id !== 'string'
      || typeof data.status !== 'string'
      || typeof data.order_type !== 'string'
      || typeof data.branch !== 'string'
    ) return state;
    return {
      view: 'bill',
      order_id: data.order_id,
      status: data.status,
      order_type: data.order_type,
      branch: data.branch,
      lines,
      total,
    };
  }

  if (
    data.view === 'payment_qr'
    && typeof data.order_id === 'string'
    && typeof data.payment_slug === 'string'
    && typeof data.status === 'string'
    && typeof data.qr_code === 'string'
  ) {
    const lines = data.lines === undefined ? undefined : pickRows(data.lines, pickLine);
    if (lines === null) return state;
    const incomingTotal = optionalNumber(data, 'total');

    return {
      view: 'payment_qr',
      order_id: data.order_id,
      payment_slug: data.payment_slug,
      status: data.status,
      qr_code: data.qr_code,
      ...(optionalString(data, 'order_type') !== undefined
        ? { order_type: optionalString(data, 'order_type') }
        : {}),
      ...(optionalString(data, 'branch') !== undefined
        ? { branch: optionalString(data, 'branch') }
        : {}),
      ...(optionalString(data, 'table_name') !== undefined
        ? { table_name: optionalString(data, 'table_name') }
        : {}),
      ...(lines !== undefined ? { lines } : {}),
      ...(incomingTotal !== undefined ? { total: incomingTotal } : {}),
      ...(optionalString(data, 'created_at') ? { created_at: optionalString(data, 'created_at') } : {}),
    };
  }

  return state;
}
