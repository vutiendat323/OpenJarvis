import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

import { CustomerDock, type DockTab } from '@/components/Kiosk/CustomerDock';
import { ProductCard, type ProductCardIntent } from '@/components/Kiosk/ProductCard';
import { useUiLanguage, type UiLanguage } from '@/hooks/useUiLanguage';
import { apiFetch } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  PICKUP_MINUTES,
  checkoutTouchCart,
  editTouchCart,
  fetchTouchPaymentStatus,
  fetchTouchTables,
  shareScreenSearch,
  type PickupMinutes,
  type TouchCartEdit,
  type TouchCartOrder,
  type TouchTable,
} from '@/lib/kioskPresentation';
import { useAgentEvents, type AgentEvent } from '@/lib/useAgentEvents';
import {
  backgroundCart,
  isSafeQrImageSource,
  menuItemDetails,
  searchMenuItems,
  paymentDeadline,
  reduceCustomerDisplay,
  waitingState,
  type CustomerDisplayLine,
  type CustomerDisplayState,
  type CustomerMenuItem,
} from './customerDisplayState';

const vnd = new Intl.NumberFormat('en-US');

type MenuDisplayState = Extract<CustomerDisplayState, { view: 'menu' }>;
type CartDisplayState = Extract<CustomerDisplayState, { view: 'cart' }>;

const emptyCartState: CartDisplayState = {
  view: 'cart',
  lines: [],
  total: 0,
  order_note: '',
  order_type: '',
  table: '',
  table_name: '',
  pickup_minutes: 0,
};

function remainingClock(ms: number): string {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function dockTabForState(state: CustomerDisplayState): DockTab {
  return state.view === 'menu' || state.view === 'waiting' ? 'menu' : 'cart';
}

function cartQuantity(lines: CustomerDisplayLine[]): number {
  return lines.reduce((total, line) => total + Math.max(0, line.quantity ?? 1), 0);
}

function money(value: number | undefined): string {
  return `${vnd.format(value ?? 0)} VND`;
}

// Striped Band Components
function StripedBand() {
  return (
    <div
      className="h-6 w-full shrink-0"
      style={{
        background: 'repeating-linear-gradient(90deg, #fae7cd, #fae7cd 14px, #cbb196 14px, #cbb196 28px)',
      }}
    />
  );
}

// Swallowtail Ribbon Header
function ColumnRibbon({
  title,
  dotsLeft = true,
  dotsRight = true,
}: {
  title: string;
  dotsLeft?: boolean;
  dotsRight?: boolean;
}) {
  return (
    <div className="relative mb-5 flex w-full min-w-0 items-center">
      {dotsLeft && (
        <div
          className="h-1.5 min-w-[12px] flex-1"
          style={{
            backgroundImage: 'radial-gradient(circle, #8c6239 1.6px, transparent 1.6px)',
            backgroundSize: '10px 6px',
            backgroundRepeat: 'repeat-x',
            backgroundPosition: 'center',
          }}
        />
      )}
      <div
        className="relative z-10 flex h-9 w-[280px] sm:w-[320px] max-w-full items-center justify-center bg-[#8c6239] px-4 sm:px-6 text-sm sm:text-[15px] font-bold tracking-[0.18em] sm:tracking-[0.25em] text-white uppercase shadow-sm shrink-0 truncate"
        style={{
          clipPath: 'polygon(0% 0%, 100% 0%, calc(100% - 14px) 50%, 100% 100%, 0% 100%, 14px 50%)',
        }}
      >
        {title}
      </div>
      {dotsRight && (
        <div
          className="h-1.5 min-w-[12px] flex-1"
          style={{
            backgroundImage: 'radial-gradient(circle, #8c6239 1.6px, transparent 1.6px)',
            backgroundSize: '10px 6px',
            backgroundRepeat: 'repeat-x',
            backgroundPosition: 'center',
          }}
        />
      )}
    </div>
  );
}

// Restaurant Editorial Header (Only on Menu View)
function RestaurantHeader({ isVi = false }: { isVi?: boolean }) {
  return (
    <header className="grid grid-cols-1 items-center gap-4 px-8 pt-5 pb-3 md:grid-cols-3 text-[#8c6239]">
      <div className="text-left text-[12px] leading-[1.42] tracking-[0.12em]">
        <div className="font-semibold uppercase">OPEN AT 10AM-10PM</div>
        <div>Club Ministère, 4TH Avenue</div>
        <div className="mt-0.5 font-semibold">FOR RESERVATION, CALL :</div>
        <div className="tracking-[0.14em]">(+1)989-466-3731</div>
      </div>

      <div className="flex flex-col items-center justify-center text-center">
        <div className="mb-0.5 text-[9px] font-semibold tracking-[0.28em]">EST. 1996</div>
        <div className="relative my-0.5 flex w-full items-center justify-center">
          <svg className="h-8 w-16 text-[#8c6239]" viewBox="0 0 80 40" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M75,35 C50,35 25,28 10,12 C20,10 28,15 32,22 C18,16 12,6 18,2 C22,10 30,16 42,24 C30,10 32,2 40,2 C44,12 50,20 58,30"/>
          </svg>
          <div className="mx-1 font-['Alex_Brush',cursive] text-4xl leading-none sm:text-5xl text-[#8c6239]">Restaurant</div>
          <svg className="h-8 w-16 text-[#8c6239]" viewBox="0 0 80 40" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M5,35 C30,35 55,28 70,12 C60,10 52,15 48,22 C62,16 68,6 62,2 C58,10 50,16 38,24 C50,10 48,2 40,2 C36,12 30,20 22,30"/>
          </svg>
        </div>
        <div className="mt-0.5 text-2xl font-bold tracking-[0.35em] uppercase leading-none sm:text-3xl text-[#8c6239]">
          {isVi ? 'THỰC ĐƠN' : 'MENU'}
        </div>
        <div className="mt-1 text-[10.5px] font-semibold tracking-[0.26em] uppercase text-[#8c6239]">BEST FOOD IN TOWN</div>
      </div>

      <div className="flex flex-col items-end justify-center text-right font-['Alex_Brush',cursive] text-3xl leading-none sm:text-4xl text-[#8c6239]">
        <div>Special</div>
        <div className="-mt-1">Dishes</div>
      </div>
    </header>
  );
}

// Editorial Footer Bar
function EditorialFooter({ showInfo = true }: { showInfo?: boolean }) {
  return (
    <footer className="mt-auto shrink-0 font-['Josefin_Sans',sans-serif]">
      {showInfo && (
        <div className="flex flex-wrap items-center justify-center gap-6 bg-[#c0a386] px-8 py-2 text-[11px] font-medium tracking-wider text-white">
          <div>@trendcoffee.vn</div>
          <div>Trend Coffee Vietnam</div>
          <div>trendcoffee.net</div>
          <div>03 NGUYEN CONG TRU STREET, THU DUC CITY</div>
        </div>
      )}
      <StripedBand />
    </footer>
  );
}

function ReceiptFooter({ isVi = false }: { isVi?: boolean }) {
  return (
    <footer className="mt-6 flex flex-wrap items-center justify-between gap-5 border-t-[1.5px] border-[#783820] pt-4 sm:pt-5 text-[#783820]">
      <div className="flex items-center gap-3 font-['Josefin_Sans',sans-serif] uppercase">
        <svg className="h-8 w-8 sm:h-9 sm:w-9 lg:h-10 lg:w-10 shrink-0" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.25" aria-hidden="true">
          <path d="M16 3 3 29h26L16 3Z" />
          <path d="M16 3v26M10 29l6-26 6 26" />
        </svg>
        <div
          aria-label="TREND COFFEE & RESTAURANT"
          className="text-[11px] sm:text-[12px] lg:text-[13px] font-bold tracking-[0.24em] leading-tight"
        >
          <div>TREND</div>
          <div className="text-[8px] sm:text-[9px] lg:text-[10px] font-semibold tracking-[0.34em]">COFFEE &amp; RESTAURANT</div>
        </div>
      </div>
      <div className="font-['Playfair_Display',serif] text-[14px] sm:text-[16px] lg:text-[18px] tracking-wide uppercase">
        {isVi ? 'CẢM ƠN QUÝ KHÁCH ĐÃ GHÉ THĂM.' : 'THANK YOU FOR DINING WITH US.'}
      </div>
    </footer>
  );
}

interface MenuCategorySection {
  title: string;
  items: CustomerMenuItem[];
}

function menuPrice(value: number | undefined): string | number {
  if (value === undefined) return '-';
  return value >= 1000 ? Math.round(value / 1000) : value;
}

function groupMenuItems(items: CustomerMenuItem[]): MenuCategorySection[] {
  const sections = new Map<string, CustomerMenuItem[]>();
  for (const item of items) {
    const title = item.category?.trim().toLocaleUpperCase('vi-VN') || 'OTHER';
    const section = sections.get(title) ?? [];
    section.push(item);
    sections.set(title, section);
  }
  return Array.from(sections, ([title, sectionItems]) => ({
    title,
    items: sectionItems,
  }));
}

function arrangeMenuSections(sections: MenuCategorySection[]): MenuCategorySection[][] {
  const orderedSections = sections
    .map((section, index) => ({ section, index }))
    .sort(
      (left, right) =>
        left.section.items.length - right.section.items.length || left.index - right.index,
    )
    .map(({ section }) => section);

  return Array.from(
    { length: Math.ceil(orderedSections.length / 3) },
    (_, index) => orderedSections.slice(index * 3, (index + 1) * 3),
  );
}

// VIEW 1: APPROVED MENU
export function MenuView({
  items,
  menuItems,
  displayMode,
  resultComplete,
  projectedCount,
  publishedCount,
  preview,
  uiLanguage = 'en',
  onSelectItem,
  searchQuery = '',
  onSearchChange,
}: {
  items: CustomerMenuItem[];
  menuItems: CustomerMenuItem[];
  displayMode: 'browse' | 'filtered';
  resultComplete: boolean;
  projectedCount: number;
  publishedCount: number;
  preview: boolean;
  uiLanguage?: UiLanguage;
  onSelectItem?: (item: CustomerMenuItem) => void;
  /** Typed on the display; filters the live catalog locally, never the agent's. */
  searchQuery?: string;
  /** Present on the live display: shows the touch search field. */
  onSearchChange?: (query: string) => void;
}) {
  const isVi = uiLanguage === 'vi';
  const demoMenuItems: CustomerMenuItem[] = [
    {
      id: 'demo-minced-beef',
      name: 'MINCED BEEF SPAGHETTI',
      price: 107000,
      note: 'Special gourmet recipe prepared fresh daily with premium ingredients.',
    },
    {
      id: 'demo-carbonara',
      name: 'SPAGHETTI CARBONARA',
      price: 150000,
      note: 'Special gourmet recipe prepared fresh daily with premium ingredients.',
    },
    {
      id: 'demo-shrimp',
      name: 'SHRIMP SPAGHETTI',
      price: 172000,
      note: 'Special gourmet recipe prepared fresh daily with premium ingredients.',
    },
  ];
  const menuSections = groupMenuItems(menuItems);
  const menuRows = arrangeMenuSections(menuSections);
  const searching = searchQuery.trim() !== '';
  const mainDishes = searching
    ? searchMenuItems(menuItems, searchQuery)
    : preview
      ? demoMenuItems
      : displayMode === 'browse'
        ? menuItems.filter((item) => item.is_top_sell === true || item.is_new === true)
        : items;
  const noMatches = searching
    ? mainDishes.length === 0
    : resultComplete && !preview && displayMode === 'filtered' && items.length === 0;

  return (
    <div
      className="flex flex-1 flex-col w-full min-w-0 px-3 sm:px-6 md:px-8 pb-6 text-[#8c6239]"
      data-projected-count={projectedCount}
      data-published-count={publishedCount}
    >
      <div
        className="grid w-full min-w-0 grid-cols-1 gap-y-8 md:grid-cols-[minmax(0,7fr)_minmax(18rem,3fr)] md:gap-x-8 lg:gap-x-12"
        data-menu-layout="asymmetric"
      >
        {/* LEFT COLUMN: MENU */}
        <div className="flex min-h-0 w-full min-w-0 flex-col">
          <ColumnRibbon title={isVi ? 'THỰC ĐƠN' : 'MENU'} dotsLeft={true} dotsRight={true} />
          <div
            className="flex max-h-[calc(100vh-16rem)] flex-1 flex-col gap-y-6 overflow-y-auto pr-2 sm:gap-y-8"
            data-menu-scroll="true"
          >
            {menuRows.map((row) => (
              <div
                key={row.map((section) => section.title).join('-')}
                className="grid grid-cols-1 gap-x-4 gap-y-6 sm:grid-cols-2 sm:gap-x-6 lg:grid-cols-3 lg:gap-x-8"
                data-menu-row
              >
                {row.map((section) => (
                  <div key={section.title} className="flex min-w-0 flex-col">
                    <h3 className="mb-2.5 text-center font-['Oswald',sans-serif] text-[18px] font-bold tracking-[0.08em] uppercase text-[#8c6239] leading-tight sm:text-[20px]">
                      {section.title}
                    </h3>
                    <div className="flex min-w-0 flex-col space-y-2">
                      {section.items.map((it) => (
                        <button
                          type="button"
                          key={it.id ?? it.name}
                          data-catalog-item
                          onClick={() => onSelectItem?.(it)}
                          className="flex w-full min-w-0 touch-manipulation items-baseline justify-between text-left text-[15px] font-bold leading-tight tracking-[0.12em] uppercase text-[#8c6239] transition-colors active:text-[#5c2912] sm:text-[16px]"
                        >
                          <span className="truncate pr-1 uppercase">{it.name}</span>
                          <span className="shrink-0 tabular-nums font-bold ml-2">{menuPrice(it.price)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT COLUMN: RECOMMENDATIONS */}
        <div className="flex min-h-0 w-full min-w-0 flex-col">
          <ColumnRibbon title={isVi ? 'GỢI Ý HÔM NAY' : 'RECOMMENDATIONS'} dotsLeft={true} dotsRight={true} />
          {onSearchChange && (
            <div
              className="relative mb-5 flex h-[44px] sm:h-[46px] w-full items-center pl-3 sm:pl-3.5 pr-1.5 sm:pr-2 drop-shadow-[0_2px_4px_rgba(61,24,6,0.18)]"
              style={{
                background: [
                  'radial-gradient(circle 16px at 0 0, transparent 16px, #8c6239 16.5px) top left',
                  'radial-gradient(circle 16px at 100% 0, transparent 16px, #8c6239 16.5px) top right',
                  'radial-gradient(circle 16px at 0 100%, transparent 16px, #8c6239 16.5px) bottom left',
                  'radial-gradient(circle 16px at 100% 100%, transparent 16px, #8c6239 16.5px) bottom right',
                ].join(','),
                backgroundSize: '51% 51%',
                backgroundRepeat: 'no-repeat',
              }}
            >
              {/* Inner vintage capsule / pill synced with menu background */}
              <div className="flex h-[32px] sm:h-[34px] flex-1 items-center rounded-full border border-[#8c6239]/30 bg-[#fae7cd] px-3.5 shadow-inner transition-colors">
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => onSearchChange(event.target.value)}
                  placeholder={isVi ? 'Tìm kiếm sản phẩm...' : 'Search menu...'}
                  aria-label={isVi ? 'Tìm kiếm sản phẩm' : 'Search menu'}
                  autoComplete="off"
                  spellCheck={false}
                  enterKeyHint="search"
                  maxLength={60}
                  className="w-full bg-transparent font-['Josefin_Sans',sans-serif] text-[13.5px] sm:text-[14px] font-normal italic tracking-wide text-[#3a1d0f] outline-none placeholder:font-['Josefin_Sans',sans-serif] placeholder:italic placeholder:text-[#9b7352]"
                />
                {searchQuery && (
                  <button
                    type="button"
                    data-testid="menu-search-clear"
                    aria-label={isVi ? 'Xóa tìm kiếm' : 'Clear search'}
                    onClick={() => onSearchChange('')}
                    className="ml-1 mr-[-4px] grid h-6 w-6 shrink-0 place-items-center rounded-full text-[#8c6239] transition-colors hover:bg-[#8c6239]/15 active:scale-95"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                      <path d="M18 6L6 18M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </div>

              {/* Vintage Magnifying Glass Icon inside Brown Plaque */}
              <div className="flex h-full w-8 sm:w-9 shrink-0 items-center justify-center text-white" aria-hidden="true">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="7" />
                  <path d="M21 21l-4.35-4.35" />
                </svg>
              </div>
            </div>
          )}
          <div
            className="flex w-full min-w-0 flex-col space-y-4"
            data-recommendations-layout="compact"
          >
            {noMatches && (
              <p className="py-12 text-center font-['Josefin_Sans',sans-serif] text-[15px] font-semibold leading-tight tracking-[0.03em] sm:text-[16px]">
                {isVi ? 'Không có kết quả phù hợp.' : 'No matching items found.'}
              </p>
            )}
            {mainDishes.map((item, index) => {
              const displayPrice =
                item.price !== undefined
                  ? item.price >= 1000
                    ? Math.round(item.price / 1000)
                    : item.price
                  : '10';
              return (
                <button
                  type="button"
                  key={item.id ?? `${item.name}-${index}`}
                  className="flex w-full min-w-0 touch-manipulation flex-col text-left transition-opacity active:opacity-70"
                  data-menu-item
                  onClick={() => onSelectItem?.(item)}
                >
                  <span className="flex items-center justify-between font-bold text-[15px] sm:text-[16px] tracking-[0.12em] uppercase text-[#8c6239] min-w-0">
                    <span className="truncate pr-2">{item.name}</span>
                    <span className="tabular-nums font-bold shrink-0 ml-4">{displayPrice}</span>
                  </span>
                  <span className="mt-0.5 block font-['Josefin_Sans',sans-serif] text-[13.5px] sm:text-[14px] font-normal italic leading-snug tracking-wide text-[#9b7352]">
                    {item.note || 'Special gourmet recipe prepared fresh daily with premium ingredients.'}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function orderTypeLabel(orderType: string, isVi = false): string {
  if (orderType === 'at-table') return isVi ? 'TẠI BÀN' : 'AT TABLE';
  if (orderType === 'take-out') return isVi ? 'MANG VỀ' : 'TAKE OUT';
  return isVi ? 'CHƯA CHỌN' : 'NOT SELECTED';
}

function englishOrderTypeLabel(orderType: string): string {
  if (orderType === 'at-table') return 'AT TABLE';
  if (orderType === 'take-out') return 'TAKE OUT';
  return 'NOT SELECTED';
}

export interface CartTouchControls {
  busy: boolean;
  /** The server's refusal code for the last tap, if any. */
  error: string | null;
  /** The last live table read; null while loading or unavailable. */
  tables: TouchTable[] | null;
  onQuantity: (lineId: string, quantity: number) => void;
  onRemove: (lineId: string) => void;
  onClear: () => void;
  onOrderType: (orderType: 'at-table' | 'take-out') => void;
  onTable: (slug: string) => void;
  onRefreshTables: () => void;
  onPickup: (minutes: PickupMinutes) => void;
  onCheckout: () => void;
}

const MAX_LINE_QUANTITY = 99;

const TOUCH_REFUSALS: Record<string, [en: string, vi: string]> = {
  voice_session_required: ['Start a chat with the assistant to order.', 'Hãy bắt đầu trò chuyện với trợ lý để đặt món.'],
  checkout_in_progress: ['Your order is being placed. Please wait a moment.', 'Đơn đang được xử lý, vui lòng chờ một chút.'],
  cart_line_not_found: ['That item just changed. Please check your cart again.', 'Món vừa thay đổi, vui lòng kiểm tra lại giỏ hàng.'],
  table_not_found: ['That table is no longer listed. Please pick another one.', 'Bàn này không còn trong danh sách, vui lòng chọn bàn khác.'],
  tables_unavailable: ['Tables could not be loaded. Please try again.', 'Chưa tải được danh sách bàn, vui lòng thử lại.'],
  cart_empty: ['Your cart is empty.', 'Giỏ hàng đang trống.'],
  order_type_required: ['Choose dine-in or take-out first.', 'Vui lòng chọn tại bàn hoặc mang về.'],
  table_required: ['Select a table first.', 'Vui lòng chọn bàn trước.'],
  checkout_failed: ['The order could not be placed. Please try again or ask the assistant.', 'Chưa đặt được đơn, vui lòng thử lại hoặc nhờ trợ lý.'],
};

function touchRefusal(code: string, isVi: boolean): string {
  const text = TOUCH_REFUSALS[code];
  if (text) return text[isVi ? 1 : 0];
  return isVi ? 'Chưa thực hiện được, vui lòng thử lại.' : 'That did not go through. Please try again.';
}

function tableStatusLabel(status: string, isVi: boolean): string {
  if (status === 'available') return isVi ? 'TRỐNG' : 'AVAILABLE';
  if (status === 'reserved') return isVi ? 'ĐÃ ĐẶT' : 'RESERVED';
  return status.toUpperCase();
}

function pickupLabel(minutes: number, isVi: boolean): string {
  if (minutes === 0) return isVi ? 'NGAY LẬP TỨC' : 'IMMEDIATELY';
  return isVi ? `${minutes} PHÚT` : `${minutes} MINUTES`;
}


const receiptStep = "grid h-7 w-7 sm:h-8 sm:w-8 shrink-0 place-items-center rounded-full border-[1.5px] border-[#783820]/70 text-[15px] sm:text-[16px] font-bold leading-none text-[#783820] transition-all hover:bg-[#783820]/10 hover:border-[#783820] active:scale-90 disabled:opacity-30 disabled:cursor-not-allowed";

// VIEW 2: LOCAL CART DRAFT
export function CartView({
  lines,
  total,
  order_note,
  order_type,
  table = '',
  table_name,
  pickup_minutes = 0,
  uiLanguage = 'en',
  controls,
}: {
  lines: CustomerDisplayLine[];
  total: number;
  order_note: string;
  order_type: string;
  table?: string;
  table_name: string;
  pickup_minutes?: number;
  uiLanguage?: UiLanguage;
  /** Present on the live display: the receipt becomes the customer's cart editor. */
  controls?: CartTouchControls;
}) {
  const isVi = uiLanguage === 'vi';
  const [reservedTable, setReservedTable] = useState<TouchTable | null>(null);
  const [openDropdown, setOpenDropdown] = useState<'order_type' | 'table' | 'pickup' | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!openDropdown) return;
    const handlePointerDown = (e: MouseEvent | TouchEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpenDropdown(null);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpenDropdown(null);
    };
    document.addEventListener('pointerdown', handlePointerDown as EventListener);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown as EventListener);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [openDropdown]);

  const busy = controls?.busy ?? false;
  const missingChoice = order_type === 'at-table' && !table
    ? (isVi ? 'CHỌN BÀN ĐỂ THANH TOÁN' : 'SELECT A TABLE TO CHECK OUT')
    : null;
  const canCheckout = !busy && lines.length > 0 && (order_type === 'at-table' || order_type === 'take-out') && missingChoice === null;
  const knownTable = controls?.tables?.some((row) => row.slug === table) ?? false;
  const currentOrderType = order_type === 'at-table' || order_type === 'take-out' ? order_type : '';
  const currentOrderTypeLabel = orderTypeLabel(currentOrderType, isVi);
  const matchedTable = controls?.tables?.find((row) => row.slug === table);
  const currentTableLabel = matchedTable
    ? `${matchedTable.name} · ${tableStatusLabel(matchedTable.status, isVi)}`
    : (table && !knownTable ? (table_name || table) : (isVi ? 'CHỌN BÀN' : 'SELECT'));
  const currentPickupLabel = pickupLabel(pickup_minutes, isVi);

  const chooseTable = (slug: string) => {
    const row = controls?.tables?.find((candidate) => candidate.slug === slug);
    if (!row || !controls) return;
    if (row.status === 'reserved') setReservedTable(row);
    else controls.onTable(row.slug);
  };

  return (
    <div className="mx-auto flex w-full max-w-[680px] lg:max-w-[880px] xl:max-w-[960px] flex-1 flex-col px-6 sm:px-10 py-6 sm:py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2 sm:pb-3">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl lg:text-6xl">
          {isVi ? 'GIỎ HÀNG' : 'CART'}
        </h1>
        <div className="text-right text-[11px] sm:text-[12px] lg:text-[13px] font-semibold tracking-wider uppercase leading-tight">
          {isVi ? 'CHƯA TẠO ĐƠN' : 'ORDER NOT CREATED'}
        </div>
      </div>

      <div className="space-y-2 pt-3 pb-6 text-[10px] font-semibold tracking-wider uppercase leading-relaxed">
        <div>
          <div>03 NGUYEN CONG TRU STREET, BINH THO WARD, THU DUC CITY</div>
          <div>ORDER@TRENDCOFFEE.VN | +84 90 123 4567</div>
          <div>WWW.TRENDCOFFEE.NET</div>
        </div>
        {controls ? (
          <div ref={dropdownRef} className="flex flex-wrap items-center gap-x-5 gap-y-2 pt-1 text-[11px]">
            {/* 1. ORDER TYPE DROPDOWN */}
            <div className="relative inline-block">
              <button
                type="button"
                disabled={busy}
                onClick={() => setOpenDropdown((cur) => (cur === 'order_type' ? null : 'order_type'))}
                className="relative inline-flex items-center gap-1 cursor-pointer hover:text-[#5c2912] focus:outline-none disabled:cursor-default disabled:opacity-50"
              >
                <span className="shrink-0">{isVi ? 'LOẠI ĐƠN' : 'ORDER TYPE'}:</span>
                <span className="font-semibold uppercase tracking-wider text-[#783820]">
                  {currentOrderTypeLabel}
                </span>
                <svg
                  className={cn(
                    "h-2.5 w-2.5 shrink-0 stroke-current text-[#783820] transition-transform duration-150",
                    openDropdown === 'order_type' && "rotate-180"
                  )}
                  viewBox="0 0 14 14"
                  fill="none"
                  aria-hidden="true"
                >
                  <path d="M3 5l4 4 4-4" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <select
                data-testid="cart-order-type"
                value={currentOrderType}
                disabled={busy}
                onChange={(event) => controls.onOrderType(event.target.value as 'at-table' | 'take-out')}
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
              >
                <option value="" disabled>{orderTypeLabel('', isVi)}</option>
                <option value="at-table">{orderTypeLabel('at-table', isVi)}</option>
                <option value="take-out">{orderTypeLabel('take-out', isVi)}</option>
              </select>

              {openDropdown === 'order_type' && (
                <div
                  className="absolute left-0 top-full z-50 mt-1 min-w-[170px] border border-[#8c6239] bg-[#fae7cd] shadow-[0_6px_20px_rgba(90,27,0,0.12)] animate-in fade-in duration-100"
                >
                  <div>
                    {(['at-table', 'take-out'] as const).map((ot, idx, arr) => {
                      const isSelected = currentOrderType === ot;
                      return (
                        <button
                          key={ot}
                          type="button"
                          onClick={() => {
                            controls.onOrderType(ot);
                            setOpenDropdown(null);
                          }}
                          className={cn(
                            "flex w-full items-center justify-between px-3 py-2 text-left font-['Josefin_Sans',sans-serif] text-[11px] font-semibold uppercase tracking-wider transition-colors",
                            idx < arr.length - 1 && "border-b border-[#8c6239]/20",
                            isSelected
                              ? "bg-[#8c6239]/20 text-[#5c2912]"
                              : "text-[#783820] hover:bg-[#8c6239]/10 hover:text-[#5c2912]"
                          )}
                        >
                          <span>{orderTypeLabel(ot, isVi)}</span>
                          {isSelected && (
                            <svg className="ml-2 h-3.5 w-3.5 shrink-0 stroke-current text-[#783820]" viewBox="0 0 16 16" fill="none">
                              <path d="M3 8.5l3.5 3.5 6.5-7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* 2. TABLE DROPDOWN */}
            {order_type === 'at-table' && (
              <div className="relative inline-block">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (openDropdown === 'table') {
                      setOpenDropdown(null);
                    } else {
                      controls.onRefreshTables();
                      setOpenDropdown('table');
                    }
                  }}
                  className="relative inline-flex items-center gap-1 cursor-pointer hover:text-[#5c2912] focus:outline-none disabled:cursor-default disabled:opacity-50"
                >
                  <span className="shrink-0">{isVi ? 'BÀN' : 'TABLE'}:</span>
                  <span className={cn(
                    "font-semibold uppercase tracking-wider",
                    matchedTable?.status === 'reserved' ? "text-[#b91c1c]" : "text-[#783820]"
                  )}>
                    {currentTableLabel}
                  </span>
                  <svg
                    className={cn(
                      "h-2.5 w-2.5 shrink-0 stroke-current transition-transform duration-150",
                      matchedTable?.status === 'reserved' ? "text-[#b91c1c]" : "text-[#783820]",
                      openDropdown === 'table' && "rotate-180"
                    )}
                    viewBox="0 0 14 14"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path d="M3 5l4 4 4-4" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                <select
                  data-testid="cart-table"
                  value={table}
                  disabled={busy}
                  onFocus={controls.onRefreshTables}
                  onChange={(event) => chooseTable(event.target.value)}
                  className="sr-only"
                  tabIndex={-1}
                  aria-hidden="true"
                >
                  <option value="" disabled>{isVi ? 'CHỌN BÀN' : 'SELECT'}</option>
                  {table && !knownTable && <option value={table}>{table_name || table}</option>}
                  {controls.tables?.map((row) => (
                    <option key={row.slug} value={row.slug}>{`${row.name} · ${tableStatusLabel(row.status, isVi)}`}</option>
                  ))}
                </select>

                {openDropdown === 'table' && (
                  <div
                    className="absolute left-0 top-full z-50 mt-1 min-w-[190px] border border-[#8c6239] bg-[#fae7cd] shadow-[0_6px_20px_rgba(90,27,0,0.12)] animate-in fade-in duration-100"
                  >
                    <div className="max-h-60 overflow-y-auto overscroll-contain scrollbar-thin scrollbar-thumb-[#8c6239]/40 scrollbar-track-transparent">
                      {(!controls.tables || controls.tables.length === 0) ? (
                        <div className="px-3 py-2 text-center text-[11px] text-[#9b7352] italic font-['Josefin_Sans',sans-serif]">
                          {isVi ? 'Đang tải danh sách bàn...' : 'Loading tables...'}
                        </div>
                      ) : (
                        controls.tables.map((row, idx, arr) => {
                          const isSelected = row.slug === table;
                          const isReserved = row.status === 'reserved';
                          const label = `${row.name} · ${tableStatusLabel(row.status, isVi)}`;
                          return (
                            <button
                              key={row.slug}
                              type="button"
                              onClick={() => {
                                chooseTable(row.slug);
                                setOpenDropdown(null);
                              }}
                              className={cn(
                                "flex w-full items-center justify-between px-3 py-2 text-left font-['Josefin_Sans',sans-serif] text-[11px] font-semibold uppercase tracking-wider transition-colors",
                                idx < arr.length - 1 && "border-b border-[#8c6239]/20",
                                isReserved
                                  ? (isSelected ? "bg-[#8c6239]/20 text-[#b91c1c]" : "text-[#b91c1c] hover:bg-[#8c6239]/10 hover:text-[#991b1b]")
                                  : (isSelected ? "bg-[#8c6239]/20 text-[#5c2912]" : "text-[#783820] hover:bg-[#8c6239]/10 hover:text-[#5c2912]")
                              )}
                            >
                              <span>{label}</span>
                              {isSelected && (
                                <svg className={cn("ml-2 h-3.5 w-3.5 shrink-0 stroke-current", isReserved ? "text-[#b91c1c]" : "text-[#783820]")} viewBox="0 0 16 16" fill="none">
                                  <path d="M3 8.5l3.5 3.5 6.5-7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                                </svg>
                              )}
                            </button>
                          );
                        })
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* 3. PICKUP DROPDOWN */}
            {order_type === 'take-out' && (
              <div className="relative inline-block">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setOpenDropdown((cur) => (cur === 'pickup' ? null : 'pickup'))}
                  className="relative inline-flex items-center gap-1 cursor-pointer hover:text-[#5c2912] focus:outline-none disabled:cursor-default disabled:opacity-50"
                >
                  <span className="shrink-0">{isVi ? 'GIỜ LẤY MÓN' : 'PICKUP'}:</span>
                  <span className="font-semibold uppercase tracking-wider text-[#783820]">
                    {currentPickupLabel}
                  </span>
                  <svg
                    className={cn(
                      "h-2.5 w-2.5 shrink-0 stroke-current text-[#783820] transition-transform duration-150",
                      openDropdown === 'pickup' && "rotate-180"
                    )}
                    viewBox="0 0 14 14"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path d="M3 5l4 4 4-4" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                <select
                  data-testid="cart-pickup-time"
                  value={pickup_minutes}
                  disabled={busy}
                  onChange={(event) => controls.onPickup(Number(event.target.value) as PickupMinutes)}
                  className="sr-only"
                  tabIndex={-1}
                  aria-hidden="true"
                >
                  {PICKUP_MINUTES.map((minutes) => (
                    <option key={minutes} value={minutes}>{pickupLabel(minutes, isVi)}</option>
                  ))}
                </select>

                {openDropdown === 'pickup' && (
                  <div
                    className="absolute left-0 top-full z-50 mt-1 min-w-[170px] border border-[#8c6239] bg-[#fae7cd] shadow-[0_6px_20px_rgba(90,27,0,0.12)] animate-in fade-in duration-100"
                  >
                    <div className="max-h-60 overflow-y-auto overscroll-contain scrollbar-thin scrollbar-thumb-[#8c6239]/40 scrollbar-track-transparent">
                      {PICKUP_MINUTES.map((minutes, idx, arr) => {
                        const isSelected = pickup_minutes === minutes;
                        return (
                          <button
                            key={minutes}
                            type="button"
                            onClick={() => {
                              controls.onPickup(minutes);
                              setOpenDropdown(null);
                            }}
                            className={cn(
                              "flex w-full items-center justify-between px-3 py-2 text-left font-['Josefin_Sans',sans-serif] text-[11px] font-semibold uppercase tracking-wider transition-colors",
                              idx < arr.length - 1 && "border-b border-[#8c6239]/20",
                              isSelected
                                ? "bg-[#8c6239]/20 text-[#5c2912]"
                                : "text-[#783820] hover:bg-[#8c6239]/10 hover:text-[#5c2912]"
                            )}
                          >
                            <span>{pickupLabel(minutes, isVi)}</span>
                            {isSelected && (
                              <svg className="ml-2 h-3.5 w-3.5 shrink-0 stroke-current text-[#783820]" viewBox="0 0 16 16" fill="none">
                                <path d="M3 8.5l3.5 3.5 6.5-7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {lines.length > 0 && (
              <button
                type="button"
                data-testid="cart-clear"
                disabled={busy}
                onClick={controls.onClear}
                className="ml-auto py-0.5 text-[11px] font-semibold tracking-wider uppercase text-[#783820] hover:text-[#5c2912] disabled:opacity-40"
              >
                {isVi ? 'XÓA TẤT CẢ' : 'CLEAR CART'}
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap gap-x-8 gap-y-1 text-[11px]">
            <div>{isVi ? 'LOẠI ĐƠN' : 'ORDER TYPE'}: {orderTypeLabel(order_type, isVi)}</div>
            {order_type === 'at-table' && table_name && <div>{isVi ? 'BÀN' : 'TABLE'}: {table_name}</div>}
          </div>
        )}
      </div>

      <div className="grid grid-cols-12 items-end border-b-[1.5px] border-[#783820] pb-1.5 text-[12px] font-bold tracking-widest uppercase">
        <div className="col-span-6">{isVi ? 'MÓN' : 'ITEM'}</div>
        <div className="col-span-2 text-center">{isVi ? 'SL' : 'QTY'}</div>
        <div className="col-span-2 text-right">{isVi ? 'ĐƠN GIÁ' : 'UNIT PRICE'}</div>
        <div className="col-span-2 text-right">{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</div>
      </div>

      <div className="space-y-2.5 py-3 text-[12.5px] font-medium tracking-wide">
        {lines.length > 0 ? (
          lines.map((line, index) => {
            const qty = line.quantity ?? 1;
            const lineTotal = line.line_total ?? ((line.unit_price ?? 0) * qty);
            const unitPrice = line.unit_price ?? (qty > 0 ? Math.round(lineTotal / qty) : 0);
            const lineId = line.line_id;
            return (
              <div
                key={lineId ?? `${line.name}-${index}`}
                className="grid grid-cols-12 items-center"
                data-cart-line={lineId ?? ''}
              >
                <div className="col-span-6 font-semibold uppercase">
                  <div className="flex items-center gap-1.5">
                    <span>{line.name}{line.size ? ` (${line.size})` : ''}</span>
                    {controls && lineId && (
                      <button
                        type="button"
                        data-testid="cart-line-remove"
                        aria-label={isVi ? `Xóa ${line.name}` : `Remove ${line.name}`}
                        disabled={busy}
                        onClick={() => controls.onRemove(lineId)}
                        className="grid h-5 w-5 shrink-0 place-items-center rounded-full border border-transparent text-[11px] leading-none text-[#783820] transition-transform hover:text-[#5c2912] active:scale-90 disabled:opacity-35"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  {line.note && (
                    <div className="mt-1 text-[10px] font-normal normal-case tracking-normal text-[#9b7352]">
                      {isVi ? 'Ghi chú' : 'Note'}: {line.note}
                    </div>
                  )}
                </div>
                {controls && lineId ? (
                  <div className="col-span-2 flex items-center justify-center gap-1 sm:gap-1.5">
                    <button
                      type="button"
                      data-testid="cart-line-decrease"
                      aria-label="−"
                      disabled={busy || qty <= 1}
                      onClick={() => controls.onQuantity(lineId, qty - 1)}
                      className={receiptStep}
                    >
                      −
                    </button>
                    <span className="min-w-6 text-center text-[13px] sm:text-[14px] font-bold tabular-nums">{qty}</span>
                    <button
                      type="button"
                      data-testid="cart-line-increase"
                      aria-label="+"
                      disabled={busy || qty >= MAX_LINE_QUANTITY}
                      onClick={() => controls.onQuantity(lineId, qty + 1)}
                      className={receiptStep}
                    >
                      +
                    </button>
                  </div>
                ) : (
                  <div className="col-span-2 text-center font-normal">{qty}</div>
                )}
                <div className="col-span-2 text-right tabular-nums">{money(unitPrice)}</div>
                <div className="col-span-2 text-right font-semibold tabular-nums">{money(lineTotal)}</div>
              </div>
            );
          })
        ) : (
          <div className="py-4 text-center text-sm font-semibold uppercase tracking-wider text-[#9b7352]">
            {isVi ? 'GIỎ HÀNG ĐANG TRỐNG.' : 'CART IS EMPTY.'}
          </div>
        )}
      </div>

      <div className="border-t-[1.5px] border-[#783820] pt-3 pb-6">
        <div className="ml-auto w-full max-w-80 space-y-1.5 text-[12px] font-semibold tracking-wider uppercase">
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</span>
            <span className="tabular-nums whitespace-nowrap">{money(total)}</span>
          </div>
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'PHÍ DỊCH VỤ (10%)' : 'SERVICE CHARGE (10%)'}</span>
            <span className="tabular-nums whitespace-nowrap">{money(0)}</span>
          </div>
          <div className="flex items-baseline justify-between border-t border-[#783820] pt-2 text-[14px] font-bold">
            <span className="whitespace-nowrap">{isVi ? 'TỔNG CỘNG (TẠM TÍNH)' : 'TOTAL ESTIMATED'}</span>
            <span className="text-[15px] font-extrabold tabular-nums whitespace-nowrap shrink-0 ml-4">{money(total)}</span>
          </div>
        </div>
        <div className={controls ? 'mt-8 flex flex-wrap items-start justify-between gap-6' : ''}>
          <div className={`${controls ? '' : 'mt-8 '}max-w-[440px] space-y-1 text-[10px] font-medium tracking-wider uppercase leading-relaxed`}>
            <div>{isVi ? 'THẺ TÍN DỤNG & GHI NỢ: VISA, MASTERCARD, NAPAS' : 'CREDIT & DEBIT CARDS: VISA, MASTERCARD, NAPAS'}</div>
            <div className="pt-2">{isVi ? 'CHUYỂN KHOẢN:' : 'BANK TRANSFER:'}</div>
            <div>{isVi ? 'TÊN TÀI KHOẢN: CONG TY CO PHAN TREND COFFEE' : 'ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE'}</div>
            <div>MB BANK: 9999.8888.68</div>
            <div className="pt-2">{isVi ? 'TRẠNG THÁI: ĐANG XỬ LÝ ĐƠN HÀNG (VUI LÒNG KIỂM TRA MÓN).' : 'STATUS: ORDER IN PROGRESS (PLEASE CHECK YOUR ITEMS).'}</div>
          </div>
          {controls && (
            <div className="flex w-full max-w-60 flex-col items-stretch gap-2">
              <button
                type="button"
                data-testid="cart-checkout"
                disabled={!canCheckout}
                onClick={controls.onCheckout}
                className="flex h-[48px] sm:h-[52px] w-full max-w-60 items-center justify-center rounded-none bg-[#5c2912] hover:bg-[#4d220e] text-[#fae7cd] font-['Josefin_Sans',sans-serif] text-[12px] font-bold tracking-[0.2em] uppercase shadow-md transition-all active:scale-[0.98] disabled:opacity-45 disabled:cursor-not-allowed cursor-pointer focus:outline-none"
              >
                {busy ? '…' : (isVi ? 'THANH TOÁN' : 'CHECKOUT')}
              </button>
              {missingChoice && lines.length > 0 && (
                <p className="text-right text-[9.5px] font-semibold tracking-wider text-[#9b7352]">{missingChoice}</p>
              )}
              {controls.error && (
                <p role="alert" className="text-right text-[11px] font-semibold normal-case tracking-normal text-[#9a2b12]">
                  {touchRefusal(controls.error, isVi)}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {order_note && (
        <div className="border-t-[1.5px] border-[#783820] pt-4 text-[11px] leading-relaxed">
          <span className="font-bold tracking-wider uppercase">{isVi ? 'GHI CHÚ ĐƠN HÀNG: ' : 'ORDER NOTE: '}</span>
          <span>{order_note}</span>
        </div>
      )}

      <ReceiptFooter isVi={isVi} />

      {reservedTable && controls && (
        <div role="alertdialog" aria-modal="true" className="fixed inset-0 z-40 flex items-center justify-center bg-[#3d1b0c]/35 p-6">
          <div className="w-full max-w-sm border-2 border-[#68341a] bg-[#fae7cd] p-7 text-center shadow-[0_18px_40px_rgba(60,25,10,0.28)]">
            <div className="font-['Playfair_Display',serif] text-2xl text-[#68341a]">
              {isVi ? `Bàn ${reservedTable.name} đã được đặt` : `Table ${reservedTable.name} is reserved`}
            </div>
            <p className="mt-3 text-sm font-semibold text-[#783820]">
              {isVi ? 'Bạn vẫn muốn chọn bàn này và ngồi chung chứ?' : 'Do you still want this table and share it?'}
            </p>
            <div className="mt-6 flex justify-center gap-3">
              <button
                type="button"
                onClick={() => setReservedTable(null)}
                className="min-h-11 border border-[#68341a] px-5 text-[12px] font-bold tracking-[0.18em] text-[#68341a] uppercase active:scale-95"
              >
                {isVi ? 'HỦY' : 'CANCEL'}
              </button>
              <button
                type="button"
                data-testid="cart-reserved-confirm"
                onClick={() => {
                  controls.onTable(reservedTable.slug);
                  setReservedTable(null);
                }}
                className="min-h-11 bg-[#5c2912] px-5 text-[12px] font-bold tracking-[0.18em] text-[#fae7cd] uppercase active:scale-95"
              >
                {isVi ? 'XÁC NHẬN CHỌN' : 'USE THIS TABLE'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function billStatusLabel(status: string, isVi: boolean): string {
  const key = status.toLowerCase();
  if (isVi && key === 'pending') return 'CHỜ XỬ LÝ';
  if (isVi && key === 'paid') return 'ĐÃ THANH TOÁN';
  return status.toUpperCase();
}

// VIEW 3: BILL (INVOICE LUXURY INVOICE)
export function BillView({
  order_id,
  branch,
  order_type,
  status,
  lines,
  total,
  uiLanguage = 'en',
}: {
  order_id: string;
  branch: string;
  order_type?: string;
  status: string;
  lines: CustomerDisplayLine[];
  total: number;
  uiLanguage?: UiLanguage;
}) {
  const isVi = uiLanguage === 'vi';
  return (
    <div className="mx-auto flex w-full max-w-[680px] lg:max-w-[880px] xl:max-w-[960px] flex-1 flex-col px-6 sm:px-10 py-6 sm:py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2 sm:pb-3">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl lg:text-6xl">
          {isVi ? 'HÓA ĐƠN' : 'INVOICE'}
        </h1>
        <div className="text-right text-[11px] sm:text-[12px] lg:text-[13px] font-semibold tracking-wider uppercase leading-tight">
          <div>{isVi ? 'MÃ HÓA ĐƠN' : 'INVOICE NO.'}: {order_id}</div>
          <div>{isVi ? 'TRẠNG THÁI' : 'STATUS'}: {billStatusLabel(status, isVi)}</div>
        </div>
      </div>

      <div className="space-y-2 pt-3 pb-6 text-[10px] font-semibold tracking-wider uppercase leading-relaxed">
        <div className="flex flex-wrap gap-x-8 gap-y-1 text-[11px]">
          <div>{isVi ? 'CHI NHÁNH' : 'BRANCH'}: {branch}</div>
          <div>{isVi ? 'LOẠI ĐƠN' : 'ORDER TYPE'}: {orderTypeLabel(order_type ?? '', isVi)}</div>
        </div>
        <div>
          <div>RESERVATIONS@TRENDCOFFEE.VN | +84 90 123 4567</div>
          <div>WWW.TRENDCOFFEE.NET</div>
        </div>
      </div>

      <div className="grid grid-cols-12 border-b-[1.5px] border-[#783820] pb-1.5 text-[12px] font-bold tracking-widest uppercase">
        <div className="col-span-6">{isVi ? 'MÓN' : 'ITEM'}</div>
        <div className="col-span-2 text-center">{isVi ? 'SL' : 'QTY'}</div>
        <div className="col-span-2 text-right">{isVi ? 'ĐƠN GIÁ' : 'UNIT PRICE'}</div>
        <div className="col-span-2 text-right">{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</div>
      </div>

      <div className="space-y-2.5 py-3 text-[12.5px] font-medium tracking-wide">
        {lines.length > 0 ? (
          lines.map((line, index) => {
            const qty = line.quantity ?? 1;
            const lineTotal = line.line_total ?? ((line.unit_price ?? 0) * qty);
            const unitPrice = line.unit_price ?? (qty > 0 ? Math.round(lineTotal / qty) : 0);
            return (
              <div key={line.line_id ?? `${line.name}-${index}`} className="grid grid-cols-12 items-start">
                <div className="col-span-6 font-semibold uppercase">
                  <div>{line.name}{line.size ? ` (${line.size})` : ''}</div>
                  {line.note && (
                    <div className="mt-1 text-[10px] font-normal normal-case tracking-normal text-[#9b7352]">
                      {isVi ? 'Ghi chú' : 'Note'}: {line.note}
                    </div>
                  )}
                </div>
                <div className="col-span-2 text-center font-normal">{qty}</div>
                <div className="col-span-2 text-right tabular-nums">{money(unitPrice)}</div>
                <div className="col-span-2 text-right font-semibold tabular-nums">{money(lineTotal)}</div>
              </div>
            );
          })
        ) : (
          <div className="py-4 text-center text-sm normal-case tracking-normal text-[#9b7352]">
            {isVi ? 'Không có dòng món nào.' : 'Provider returned no line items.'}
          </div>
        )}
      </div>

      <div className="border-t-[1.5px] border-[#783820] pt-3 pb-6">
        <div className="ml-auto w-full max-w-80 space-y-1.5 text-[12px] font-semibold tracking-wider uppercase">
          {total !== undefined && (
            <div className="flex justify-between text-[10px]">
              <span>{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</span>
              <span className="tabular-nums whitespace-nowrap">{money(total)}</span>
            </div>
          )}
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'PHÍ DỊCH VỤ (10%)' : 'SERVICE CHARGE (10%)'}</span>
            <span className="tabular-nums whitespace-nowrap">{money(0)}</span>
          </div>
          <div className="flex items-baseline justify-between border-t border-[#783820] pt-2 text-[14px] font-bold">
            <span className="whitespace-nowrap">{isVi ? 'TỔNG TIỀN THANH TOÁN' : 'TOTAL AMOUNT DUE'}</span>
            <span className="text-[15px] font-extrabold tabular-nums whitespace-nowrap shrink-0 ml-4">{money(total)}</span>
          </div>
        </div>
        <div className="mt-8 max-w-[440px] space-y-1 text-[10px] font-medium tracking-wider uppercase leading-relaxed">
          <div>{isVi ? 'THẺ TÍN DỤNG & GHI NỢ: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS' : 'CREDIT & DEBIT CARDS: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS'}</div>
          <div className="pt-2">{isVi ? 'CHUYỂN KHOẢN:' : 'BANK TRANSFER:'}</div>
          <div>{isVi ? 'TÊN TÀI KHOẢN: CONG TY CO PHAN TREND COFFEE' : 'ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE'}</div>
          <div>MB BANK: 9999.8888.68</div>
          <div className="pt-2">{isVi ? 'THANH TOÁN TIỀN MẶT: TẠI QUẦY.' : 'CASH PAYMENTS: IN-PERSON ONLY.'}</div>
        </div>
      </div>

      <ReceiptFooter isVi={isVi} />
    </div>
  );
}

// VIEW 4: PAYMENT QR RECEIPT
export function PaymentQrView({
  qr_code,
  total,
  order_id,
  status,
  order_type,
  branch,
  table_name,
  lines = [],
  uiLanguage = 'en',
  created_at,
}: {
  qr_code: string;
  total?: number;
  order_id: string;
  status?: string;
  order_type?: string;
  branch?: string;
  table_name?: string;
  lines?: CustomerDisplayLine[];
  uiLanguage?: UiLanguage;
  created_at?: string;
}) {
  const isVi = uiLanguage === 'vi';
  const isImage = isSafeQrImageSource(qr_code);
  const [shownAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const deadline = paymentDeadline(created_at, shownAt);
  const expired = now >= deadline;
  useEffect(() => {
    if (expired) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [expired]);
  const statusLabel = status?.toLowerCase() === 'pending'
    ? (isVi ? 'CHỜ THANH TOÁN' : 'PENDING PAYMENT')
    : (status?.toUpperCase() ?? (isVi ? 'KHÔNG CÓ TRẠNG THÁI' : 'PAYMENT STATUS UNAVAILABLE'));

  return (
    <div className="mx-auto flex w-full max-w-[680px] lg:max-w-[880px] xl:max-w-[960px] flex-1 flex-col px-6 sm:px-10 py-6 sm:py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2 sm:pb-3">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl lg:text-6xl">
          {isVi ? 'HÓA ĐƠN' : 'INVOICE'}
        </h1>
        <div className="text-right text-[11px] sm:text-[12px] lg:text-[13px] font-semibold tracking-wider uppercase leading-tight">
          <div>{isVi ? 'MÃ HÓA ĐƠN' : 'INVOICE NO.'}: {order_id}</div>
          <div>{isVi ? 'TRẠNG THÁI' : 'STATUS'}: {statusLabel}</div>
        </div>
      </div>

      <div className="space-y-2 sm:space-y-2.5 pt-3 pb-6 text-[10px] sm:text-[11px] lg:text-[12px] font-semibold tracking-wider uppercase leading-relaxed">
        <div className="flex flex-wrap gap-x-8 gap-y-1 text-[11px] sm:text-[12px] lg:text-[13px]">
          {branch && <div>{isVi ? 'CHI NHÁNH' : 'BRANCH'}: {branch}</div>}
          {order_type && <div>{isVi ? 'LOẠI ĐƠN' : 'ORDER TYPE'}: {orderTypeLabel(order_type, isVi)}</div>}
          {order_type === 'at-table' && table_name && <div>{isVi ? 'BÀN' : 'TABLE'}: {table_name}</div>}
        </div>
        <div>
          <div>03 NGUYEN CONG TRU STREET, BINH THO WARD, THU DUC CITY</div>
          <div>RESERVATIONS@TRENDCOFFEE.VN | +84 90 123 4567</div>
          <div>WWW.TRENDCOFFEE.NET</div>
        </div>
      </div>

      <div className="grid grid-cols-12 border-b-[1.5px] border-[#783820] pb-1.5 text-[12px] sm:text-[13px] lg:text-[14px] font-bold tracking-widest uppercase">
        <div className="col-span-6">{isVi ? 'MÓN' : 'ITEM'}</div>
        <div className="col-span-2 text-center">{isVi ? 'SL' : 'QTY'}</div>
        <div className="col-span-2 text-right">{isVi ? 'ĐƠN GIÁ' : 'UNIT PRICE'}</div>
        <div className="col-span-2 text-right">{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</div>
      </div>

      <div className="space-y-2.5 sm:space-y-3 py-3 text-[12.5px] sm:text-[13.5px] lg:text-[15px] font-medium tracking-wide">
        {lines.length > 0 ? lines.map((line, index) => {
          const qty = line.quantity ?? 1;
          const lineTotal = line.line_total ?? ((line.unit_price ?? 0) * qty);
          const unitPrice = line.unit_price ?? (qty > 0 ? Math.round(lineTotal / qty) : 0);
          return (
            <div key={line.line_id ?? `${line.name}-${index}`} className="grid grid-cols-12 items-start">
              <div className="col-span-6 font-semibold uppercase">
                <div>{line.name}{line.size ? ` (${line.size})` : ''}</div>
                {line.note && (
                  <div className="mt-1 text-[10px] sm:text-[11px] font-normal normal-case tracking-normal text-[#9b7352]">
                    {isVi ? 'Ghi chú' : 'Note'}: {line.note}
                  </div>
                )}
              </div>
              <div className="col-span-2 text-center font-normal">{qty}</div>
              <div className="col-span-2 text-right tabular-nums">{money(unitPrice)}</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">{money(lineTotal)}</div>
            </div>
          );
        }) : (
          <div className="py-3 text-center text-[11px] sm:text-[12px] font-semibold tracking-wider uppercase text-[#9b7352]">
            {isVi ? 'KHÔNG CÓ CHI TIẾT ĐƠN HÀNG.' : 'ORDER DETAILS UNAVAILABLE.'}
          </div>
        )}
      </div>

      <div className="border-t-[1.5px] border-[#783820] pt-3">
        <div className="ml-auto w-full max-w-80 sm:max-w-96 space-y-1.5 sm:space-y-2 text-[12px] sm:text-[13px] lg:text-[14px] font-semibold tracking-wider uppercase">
          {total !== undefined && (
            <div className="flex justify-between text-[10px] sm:text-[11px] lg:text-[12px]">
              <span>{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</span>
              <span className="tabular-nums whitespace-nowrap">{money(total)}</span>
            </div>
          )}
          <div className="flex justify-between text-[10px] sm:text-[11px] lg:text-[12px]">
            <span>{isVi ? 'PHÍ DỊCH VỤ (10%)' : 'SERVICE CHARGE (10%)'}</span>
            <span className="tabular-nums whitespace-nowrap">{money(0)}</span>
          </div>
          <div className="flex items-baseline justify-between border-t border-[#783820] pt-2 text-[14px] sm:text-[16px] lg:text-[17px] font-bold">
            <span className="whitespace-nowrap">{isVi ? 'TỔNG TIỀN THANH TOÁN' : 'TOTAL AMOUNT DUE'}</span>
            <span className="text-[15px] sm:text-[17px] lg:text-[19px] font-extrabold tabular-nums whitespace-nowrap shrink-0 ml-4">
              {total !== undefined ? money(total) : '—'}
            </span>
          </div>
        </div>

        <div className="mt-5 sm:mt-6 grid grid-cols-12 items-end gap-6 sm:gap-8">
          <div className="col-span-7 space-y-1 sm:space-y-1.5 text-[9.5px] sm:text-[11px] lg:text-[12px] font-medium tracking-wider uppercase leading-relaxed">
            <div>{isVi ? 'THẺ TÍN DỤNG & GHI NỢ: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS' : 'CREDIT & DEBIT CARDS: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS'}</div>
            <div className="pt-2">{isVi ? 'CHUYỂN KHOẢN:' : 'BANK TRANSFER:'}</div>
            <div>{isVi ? 'TÊN TÀI KHOẢN: CONG TY CO PHAN TREND COFFEE' : 'ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE'}</div>
            <div>MB BANK: 9999.8888.68</div>
          </div>
          <div className="col-span-5 flex flex-col items-end">
            <div className="flex flex-col items-center gap-1">
              <div
                data-testid="payment-countdown"
                className={`text-center text-[10px] sm:text-[11px] lg:text-[12px] font-bold tracking-wider uppercase ${expired ? 'text-[#9a2b12]' : 'whitespace-nowrap'}`}
              >
                {expired
                  ? (isVi ? 'HẾT THỜI GIAN THANH TOÁN. VUI LÒNG NHỜ TRỢ LÝ HỖ TRỢ.' : 'PAYMENT TIME EXPIRED. PLEASE ASK THE ASSISTANT.')
                  : `${isVi ? 'THỜI GIAN THANH TOÁN CÒN LẠI' : 'PAY WITHIN'}: ${remainingClock(deadline - now)}`}
              </div>
              {expired ? null : isImage ? (
                <img
                  src={qr_code}
                  alt="Verified payment QR code"
                  className="h-36 w-36 sm:h-44 sm:w-44 lg:h-52 lg:w-52 object-contain mix-blend-multiply"
                />
              ) : (
                <div className="w-36 sm:w-44 lg:w-52 border border-[#c4ab91] px-3 py-5 text-center text-[9px] sm:text-[10px] lg:text-[11px] font-semibold tracking-wider uppercase">
                  {isVi ? 'KHÔNG THỂ HIỂN THỊ MÃ QR XÁC THỰC.' : 'UNABLE TO DISPLAY A VERIFIED QR CODE.'}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <ReceiptFooter isVi={isVi} />
    </div>
  );
}

// VIEW 5: WAITING (VINTAGE POSTER - NO CORNER BRACKETS - Trend COFFEE)
function WaitingView() {
  return (
    <div className="flex flex-1 items-center justify-center px-8 py-8">
      <div className="relative w-full max-w-[580px] p-6 sm:p-8">
        <div className="relative border-[2px] border-[#8c6239] bg-transparent p-6 sm:p-9">
          <div className="mb-2 flex items-center justify-center gap-3 px-10">
            <div className="h-[2px] flex-1 bg-[#8c6239]" />
            <div className="flex items-center gap-2 font-['Josefin_Sans',sans-serif]">
              <span className="text-2xl font-extrabold tracking-tight text-[#5c3717] sm:text-3xl">Trend</span>
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#5c3717]" />
              <span className="text-xs font-medium tracking-[0.24em] text-[#9b7352] uppercase sm:text-[13px]">COFFEE</span>
            </div>
            <div className="h-[2px] flex-1 bg-[#8c6239]" />
          </div>

          <div className="my-4 text-center font-['Josefin_Sans',sans-serif]">
            <div className="mb-1 text-sm font-semibold tracking-[0.28em] text-[#8c6239] uppercase sm:text-base">
              CLASSIC RESTAURANT
            </div>
            <div className="my-2 text-6xl font-extrabold tracking-[0.15em] uppercase leading-[0.88] text-[#8c6239] sm:text-7xl lg:text-[84px]">
              <div>BREAK</div>
              <div>FAST</div>
            </div>
          </div>

          <div className="relative mt-2 grid min-h-[160px] grid-cols-2 items-start gap-4 pt-4">
            <div className="space-y-3.5 pl-1 text-left font-['Josefin_Sans',sans-serif]">
              <div className="space-y-2.5 text-xs font-bold tracking-[0.16em] uppercase text-[#8c6239] sm:text-[13px]">
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#8c6239]" />
                  <span>ORANGE JUICE</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#8c6239]" />
                  <span>CROISSANT</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#8c6239]" />
                  <span>INFUSION</span>
                </div>
              </div>

              <div className="pt-1.5">
                <div className="text-4xl font-bold tracking-tight leading-none text-[#8c6239] sm:text-[44px]">
                  $12
                </div>
              </div>

              <div className="flex items-center gap-1.5 pt-2 text-[10.5px] font-semibold text-[#8c6239]">
                <div>
                  <span className="block text-[8.5px] tracking-wider uppercase opacity-75">Delivery</span>
                  <span className="tracking-wider">00 54 9 34154777</span>
                </div>
              </div>
            </div>

            <div className="absolute top-4 bottom-2 left-1/2 w-[1.5px] -translate-x-1/2 bg-[#8c6239]" />

            <div className="space-y-3 pl-3 text-left font-['Josefin_Sans',sans-serif]">
              <p className="text-[11.5px] font-medium leading-[1.4] text-[#9b7352]">
                Start your day with fresh coffee and a crisp French croissant.
              </p>

              <div className="flex items-center gap-2 pt-1 text-[#8c6239]">
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-[#8c6239] text-[10px] font-bold">f</span>
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-[#8c6239] text-[10px] font-bold">ig</span>
                <span className="flex h-6 w-6 items-center justify-center rounded-full border border-[#8c6239] text-[10px] font-bold">tt</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function CustomerDisplayPage({
  uiLanguage: forcedLanguage,
}: {
  uiLanguage?: UiLanguage;
} = {}) {
  const { language: storedLanguage, setLanguage: setStoredLanguage } = useUiLanguage();
  const [searchParams] = useSearchParams();
  const [urlLanguage, setUrlLanguage] = useState<UiLanguage | null>(() => {
    const raw = searchParams.get('lang')?.trim().toLowerCase();
    return raw === 'vi' || raw === 'en' ? (raw as UiLanguage) : null;
  });
  const uiLanguage = forcedLanguage ?? urlLanguage ?? storedLanguage;
  const isVi = uiLanguage === 'vi';
  const sessionId = searchParams.get('session')?.trim() || undefined;
  const preview = searchParams.get('preview')?.trim();
  const [state, setState] = useState<CustomerDisplayState>(() => {
    if (preview === 'menu') return {
      view: 'menu',
      items: [],
      menuItems: [],
      displayMode: 'filtered',
      resultComplete: false,
      projectedCount: 0,
      publishedCount: 0,
      preview: true,
    };
    if (preview === 'cart') return {
      view: 'cart',
      lines: [
        { line_id: 'line-demo-1', name: 'Tiramisu choco đá xay (tiêu chuẩn)', quantity: 1, unit_price: 65000, line_total: 65000 },
      ],
      total: 65000,
      order_note: '',
      order_type: 'at-table',
      table: 'table-8',
      table_name: '8',
      pickup_minutes: 0,
    };
    if (preview === 'waiting') return { view: 'waiting' };
    return waitingState;
  });
  const [menuSnapshot, setMenuSnapshot] = useState<MenuDisplayState | null>(null);
  const [cartSnapshot, setCartSnapshot] = useState<CartDisplayState | null>(null);
  const [manualTab, setManualTab] = useState<DockTab | null>(null);
  const [selectedItem, setSelectedItem] = useState<CustomerMenuItem | null>(null);
  const [menuSearch, setMenuSearch] = useState('');
  const [touchBusy, setTouchBusy] = useState(false);
  const [touchError, setTouchError] = useState<string | null>(null);
  const [tables, setTables] = useState<TouchTable[] | null>(() =>
    preview === 'cart'
      ? Array.from({ length: 19 }, (_, i) => {
          const num = i + 1;
          const status = num <= 5 || num === 10 ? 'reserved' : 'available';
          return { slug: `table-${num}`, name: `${num}`, status: status as 'reserved' | 'available' };
        })
      : null,
  );

  useEffect(() => {
    let active = true;
    const fetchLanguage = async () => {
      try {
        const res = await apiFetch('/api/kiosk/language');
        if (res.ok) {
          const payload = (await res.json()) as { language?: string };
          if (active && (payload.language === 'en' || payload.language === 'vi')) {
            const nextLang = payload.language as UiLanguage;
            setUrlLanguage(null);
            setStoredLanguage(nextLang);
          }
        }
      } catch {
        // Safe to ignore in test or offline environments
      }
    };
    void fetchLanguage();
    const interval = setInterval(fetchLanguage, 1500);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [setStoredLanguage]);

  useEffect(() => {
    if (preview === 'menu') {
      setState({
        view: 'menu',
        items: [],
        menuItems: [],
        displayMode: 'filtered',
        resultComplete: false,
        projectedCount: 0,
        publishedCount: 0,
        preview: true,
      });
    } else if (preview === 'cart') {
      setState({
        view: 'cart',
        lines: [
          { line_id: 'line-demo-1', name: 'Tiramisu choco đá xay (tiêu chuẩn)', quantity: 1, unit_price: 65000, line_total: 65000 },
        ],
        total: 65000,
        order_note: '',
        order_type: 'at-table',
        table: 'table-8',
        table_name: '8',
        pickup_minutes: 0,
      });
      setTables(
        Array.from({ length: 19 }, (_, i) => {
          const num = i + 1;
          const status = num <= 5 || num === 10 ? 'reserved' : 'available';
          return { slug: `table-${num}`, name: `${num}`, status: status as 'reserved' | 'available' };
        }),
      );
    } else {
      setState(waitingState);
    }
    setManualTab(null);
    setSelectedItem(null);
    setMenuSearch('');
    if (preview !== 'cart') setTables(null);
    setTouchError(null);
  }, [sessionId, preview]);

  useEffect(() => {
    if (state.view === 'menu') setMenuSnapshot(state);
    if (state.view === 'cart') setCartSnapshot(state);
  }, [state]);

  const handleEvent = useCallback((event: AgentEvent) => {
    if (!sessionId) return;
    // "Add to cart" while browsing: only the dock badge (or an open receipt)
    // changes; the screen, tab and any open product card stay put.
    const savedCart = backgroundCart(event, sessionId);
    if (savedCart) {
      setCartSnapshot(savedCart);
      setState((current) => reduceCustomerDisplay(current, event, sessionId));
      return;
    }
    setManualTab(null);
    if (event.type === 'display_update' && event.data) {
      const data = event.data as Record<string, unknown>;
      if (data.ui_language === 'en' || data.ui_language === 'vi') {
        const nextLang = data.ui_language as UiLanguage;
        setUrlLanguage(null);
        setStoredLanguage(nextLang);
      }
      if (data.presentation_session_id === sessionId && typeof data.view === 'string') {
        // The product card belongs to the menu; any other screen replaces it.
        if (data.view !== 'menu') setSelectedItem(null);
        // Voice stays primary: a new spoken search (or the next customer)
        // replaces whatever was typed, so the screen matches the agent again.
        if (data.view === 'menu' || data.view === 'none') setMenuSearch('');
        // A reset starts the next customer: forget the previous one's cart.
        if (data.view === 'none') {
          setMenuSnapshot(null);
          setCartSnapshot(null);
          setTables(null);
        }
        // Showing the payment QR settles the draft on the server.
        if (data.view === 'payment_qr') setCartSnapshot(null);
        setTouchError(null);
      }
    }
    setState((current) => reduceCustomerDisplay(current, event, sessionId));
  }, [sessionId, setStoredLanguage]);

  useAgentEvents(undefined, handleEvent, ['display_update'], sessionId);

  const closeProductCard = useCallback(() => setSelectedItem(null), []);

  const applyCart = useCallback((cart: CartDisplayState) => {
    setCartSnapshot(cart);
    setState((current) => (current.view === 'cart' ? cart : current));
  }, []);

  const submitTouchOrder = useCallback(async (order: TouchCartOrder, intent: ProductCardIntent) => {
    if (!sessionId) throw new Error('presentation_session_not_found');
    applyCart(await editTouchCart(sessionId, { action: 'add', ...order }));
    setSelectedItem(null);
    if (intent === 'buy') setManualTab('cart');
  }, [sessionId, applyCart]);

  // One tap at a time: the receipt locks until the server answers.
  const runTouch = useCallback(async (task: (session: string) => Promise<void>) => {
    if (!sessionId) return;
    setTouchBusy(true);
    setTouchError(null);
    try {
      await task(sessionId);
    } catch (error) {
      setTouchError(error instanceof Error ? error.message : 'touch_unavailable');
    } finally {
      setTouchBusy(false);
    }
  }, [sessionId]);

  const editCart = useCallback((edit: TouchCartEdit) => {
    void runTouch(async (session) => applyCart(await editTouchCart(session, edit)));
  }, [runTouch, applyCart]);

  const refreshTables = useCallback(() => {
    if (!sessionId || preview === 'cart') return;
    fetchTouchTables(sessionId).then(setTables).catch(() => setTouchError('tables_unavailable'));
  }, [sessionId, preview]);

  const cartControls = useMemo<CartTouchControls>(() => ({
    busy: touchBusy,
    error: touchError,
    tables,
    onQuantity: (lineId, quantity) => editCart({ action: 'update', line_id: lineId, quantity }),
    onRemove: (lineId) => editCart({ action: 'remove', line_id: lineId }),
    onClear: () => editCart({ action: 'clear' }),
    onOrderType: (orderType) => editCart({ action: 'set_order_type', order_type: orderType }),
    onTable: (table) => editCart({ action: 'set_table', table }),
    onRefreshTables: refreshTables,
    onPickup: (minutes) => editCart({ action: 'set_pickup_time', pickup_minutes: minutes }),
    onCheckout: () => {
      void runTouch(async (session) => {
        await checkoutTouchCart(session);
        // The recipe has published the bill and QR; follow them.
        setManualTab(null);
      });
    },
  }), [touchBusy, touchError, tables, editCart, refreshTables, runTouch]);

  // Poll the merchant like its own payment page does; the server shows the
  // paid bill, which ends this polling.
  const paymentOrder = state.view === 'payment_qr' ? state.order_id : null;
  const paymentCreatedAt = state.view === 'payment_qr' ? state.created_at : undefined;
  useEffect(() => {
    if (!sessionId || !paymentOrder) return;
    const deadline = paymentDeadline(paymentCreatedAt, Date.now());
    const timer = setInterval(() => {
      if (Date.now() >= deadline) {
        clearInterval(timer);
        return;
      }
      fetchTouchPaymentStatus(sessionId).catch(() => {});
    }, 2000);
    return () => clearInterval(timer);
  }, [sessionId, paymentOrder, paymentCreatedAt]);

  const changeTab = useCallback((tab: DockTab) => {
    setSelectedItem(null);
    setManualTab(tab);
  }, []);

  const activeTab = manualTab ?? dockTabForState(state);
  const visibleState = manualTab === 'menu' && menuSnapshot
    ? menuSnapshot
    : manualTab === 'cart'
      ? (cartSnapshot ?? emptyCartState)
      : state;
  const cartLines = state.view === 'cart' ? state.lines : (cartSnapshot?.lines ?? []);
  const tablesNeeded = visibleState.view === 'cart' && visibleState.order_type === 'at-table';

  useEffect(() => {
    if (tablesNeeded) refreshTables();
  }, [tablesNeeded, refreshTables]);

  // Tell the Voice agent which rows a typed search shows, so "the second one"
  // means what the customer sees. Sent once typing pauses; [] when none shows.
  const searchKey = visibleState.view === 'menu' && menuSearch.trim()
    ? searchMenuItems(visibleState.menuItems, menuSearch).flatMap((item) => (item.id ? [item.id] : [])).join('\n')
    : '';
  const sharedSearchKey = useRef('');
  useEffect(() => {
    if (!sessionId || searchKey === sharedSearchKey.current) return;
    const timer = setTimeout(() => {
      sharedSearchKey.current = searchKey;
      shareScreenSearch(sessionId, searchKey ? searchKey.split('\n') : []).catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [sessionId, searchKey]);

  return (
    <div className="h-screen w-full bg-[#fae7cd] text-[#8c6239] font-['Josefin_Sans',sans-serif] flex flex-col justify-between overflow-x-hidden overflow-y-auto selection:bg-[#8c6239] selection:text-white">
      {/* Top Striped Band on Full Page */}
      <StripedBand />

      {/* Main Container */}
      <main className="w-full flex-1 flex flex-col justify-between max-w-[1440px] xl:max-w-[1600px] mx-auto pt-2 pb-28 px-2 sm:px-4 md:px-6">
        {!sessionId && !preview ? (
          <div className="my-auto flex flex-col items-center justify-center p-8 text-center">
            <h2 className="font-['Alex_Brush',cursive] text-5xl text-[#8c6239] mb-2">Trend Coffee</h2>
            <p className="text-sm font-semibold tracking-widest uppercase text-[#9b7352]">
              Ready to serve &bull; Waiting for display-session connection
            </p>
          </div>
        ) : (
          <>
            {visibleState.view === 'menu' && (
              <>
                <RestaurantHeader isVi={isVi} />
                <MenuView
                  items={visibleState.items}
                  menuItems={visibleState.menuItems}
                  displayMode={visibleState.displayMode}
                  resultComplete={visibleState.resultComplete}
                  projectedCount={visibleState.projectedCount}
                  publishedCount={visibleState.publishedCount}
                  preview={visibleState.preview}
                  uiLanguage={uiLanguage}
                  onSelectItem={(item) => setSelectedItem(menuItemDetails(item, visibleState.menuItems))}
                  searchQuery={menuSearch}
                  onSearchChange={sessionId ? setMenuSearch : undefined}
                />
              </>
            )}

            {visibleState.view === 'cart' && (
              <CartView
                lines={visibleState.lines}
                total={visibleState.total}
                order_note={visibleState.order_note}
                order_type={visibleState.order_type}
                table={visibleState.table}
                table_name={visibleState.table_name}
                pickup_minutes={visibleState.pickup_minutes}
                uiLanguage={uiLanguage}
                controls={sessionId ? cartControls : undefined}
              />
            )}

            {visibleState.view === 'bill' && (
              <BillView
                order_id={visibleState.order_id}
                branch={visibleState.branch}
                order_type={visibleState.order_type}
                status={visibleState.status}
                lines={visibleState.lines}
                total={visibleState.total}
                uiLanguage={uiLanguage}
              />
            )}

            {visibleState.view === 'payment_qr' && (
              <PaymentQrView
                qr_code={visibleState.qr_code}
                total={visibleState.total}
                order_id={visibleState.order_id}
                status={visibleState.status}
                order_type={visibleState.order_type}
                branch={visibleState.branch}
                table_name={visibleState.table_name}
                lines={visibleState.lines}
                uiLanguage={uiLanguage}
                created_at={visibleState.created_at}
              />
            )}

            {visibleState.view === 'waiting' && (
              <WaitingView />
            )}
          </>
        )}
      </main>

      {manualTab === 'help' && (
        <div role="dialog" aria-modal="true" aria-label={isVi ? 'Hỗ trợ khách hàng' : 'Customer help'} className="fixed inset-0 z-40 flex items-center justify-center bg-[#3d1b0c]/35 p-6">
          <div className="w-full max-w-md border-2 border-[#68341a] bg-[#fae7cd] p-8 text-center shadow-[0_18px_40px_rgba(60,25,10,0.28)]">
            <div className="font-['Playfair_Display',serif] text-3xl tracking-wide text-[#68341a]">{isVi ? 'Cần hỗ trợ?' : 'Need a hand?'}</div>
            <p className="mt-3 text-sm font-semibold tracking-wide text-[#783820]">{isVi ? 'Bạn có thể nói với trợ lý để xem thực đơn, cập nhật giỏ hàng hoặc thanh toán.' : 'You can ask the voice assistant to browse the menu, update your cart, or start checkout.'}</p>
            <button type="button" onClick={() => setManualTab(null)} className="mt-6 border border-[#68341a] px-5 py-2 font-['Playfair_Display',serif] text-sm font-bold tracking-[0.18em] text-[#68341a] active:scale-95">{isVi ? 'ĐÓNG' : 'CLOSE'}</button>
          </div>
        </div>
      )}

      {selectedItem && visibleState.view === 'menu' && (
        <ProductCard
          key={selectedItem.id ?? selectedItem.name}
          item={selectedItem}
          uiLanguage={uiLanguage}
          onClose={closeProductCard}
          onSubmit={submitTouchOrder}
        />
      )}

      <CustomerDock
        activeTab={activeTab}
        cartCount={cartQuantity(cartLines)}
        onTabChange={changeTab}
        uiLanguage={uiLanguage}
      />

      {/* Bottom Editorial Footer */}
      <EditorialFooter showInfo={visibleState.view !== 'cart'} />
    </div>
  );
}
