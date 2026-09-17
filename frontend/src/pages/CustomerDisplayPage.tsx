import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { CustomerDock, type DockTab } from '@/components/Kiosk/CustomerDock';
import { useUiLanguage, type UiLanguage } from '@/hooks/useUiLanguage';
import { useAgentEvents, type AgentEvent } from '@/lib/useAgentEvents';
import {
  isSafeQrImageSource,
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
};

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
    <footer className="mt-6 flex flex-wrap items-center justify-between gap-5 border-t-[1.5px] border-[#783820] pt-4 text-[#783820]">
      <div className="flex items-center gap-3 font-['Josefin_Sans',sans-serif] uppercase">
        <svg className="h-8 w-8 shrink-0" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.25" aria-hidden="true">
          <path d="M16 3 3 29h26L16 3Z" />
          <path d="M16 3v26M10 29l6-26 6 26" />
        </svg>
        <div
          aria-label="TREND COFFEE & RESTAURANT"
          className="text-[11px] font-bold tracking-[0.24em] leading-tight"
        >
          <div>TREND</div>
          <div className="text-[8px] font-semibold tracking-[0.34em]">COFFEE &amp; RESTAURANT</div>
        </div>
      </div>
      <div className="font-['Playfair_Display',serif] text-[14px] tracking-wide uppercase">
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
}: {
  items: CustomerMenuItem[];
  menuItems: CustomerMenuItem[];
  displayMode: 'browse' | 'filtered';
  resultComplete: boolean;
  projectedCount: number;
  publishedCount: number;
  preview: boolean;
  uiLanguage?: UiLanguage;
}) {
  const isVi = uiLanguage === 'vi';
  const demoMenuItems: CustomerMenuItem[] = [
    {
      name: 'MINCED BEEF SPAGHETTI',
      price: 107000,
      note: 'Special gourmet recipe prepared fresh daily with premium ingredients.',
    },
    {
      name: 'SPAGHETTI CARBONARA',
      price: 150000,
      note: 'Special gourmet recipe prepared fresh daily with premium ingredients.',
    },
    {
      name: 'SHRIMP SPAGHETTI',
      price: 172000,
      note: 'Special gourmet recipe prepared fresh daily with premium ingredients.',
    },
  ];
  const menuSections = groupMenuItems(menuItems);
  const menuRows = arrangeMenuSections(menuSections);
  const mainDishes = preview
    ? demoMenuItems
    : displayMode === 'browse'
      ? menuItems.filter((item) => item.is_top_sell === true || item.is_new === true)
      : items;

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
                        <div
                          key={it.id ?? it.name}
                          data-catalog-item
                          className="flex min-w-0 items-baseline justify-between text-[15px] font-semibold leading-tight tracking-[0.03em] text-[#8c6239] sm:text-[16px]"
                        >
                          <span className="truncate pr-1">{it.name}</span>
                          <span className="shrink-0 tabular-nums font-bold">{menuPrice(it.price)}</span>
                        </div>
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
          <div
            className="flex w-full min-w-0 flex-col space-y-4"
            data-recommendations-layout="compact"
          >
            {resultComplete && !preview && displayMode === 'filtered' && items.length === 0 && (
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
                <div
                  key={item.id ?? `${item.name}-${index}`}
                  className="flex min-w-0 flex-col"
                  data-menu-item
                >
                  <div className="flex items-center justify-between font-bold text-[15px] sm:text-[16px] tracking-[0.12em] uppercase text-[#8c6239] min-w-0">
                    <span className="truncate pr-2">{item.name}</span>
                    <span className="tabular-nums font-bold shrink-0 ml-4">{displayPrice}</span>
                  </div>
                  <p className="mt-0.5 font-['Cormorant_Garamond',serif] text-[14px] sm:text-[14.5px] italic leading-tight text-[#9b7352]">
                    {item.note || 'Special gourmet recipe prepared fresh daily with premium ingredients.'}
                  </p>
                </div>
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

// VIEW 2: LOCAL CART DRAFT
export function CartView({
  lines,
  total,
  order_note,
  order_type,
  table_name,
  uiLanguage = 'en',
}: {
  lines: CustomerDisplayLine[];
  total: number;
  order_note: string;
  order_type: string;
  table_name: string;
  uiLanguage?: UiLanguage;
}) {
  const isVi = uiLanguage === 'vi';
  return (
    <div className="mx-auto flex w-full max-w-[680px] flex-1 flex-col px-10 py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl">
          {isVi ? 'GIỎ HÀNG' : 'CART'}
        </h1>
        <div className="text-right text-[11px] font-semibold tracking-wider uppercase leading-tight">
          {isVi ? 'CHƯA TẠO ĐƠN' : 'ORDER NOT CREATED'}
        </div>
      </div>

      <div className="space-y-2 pt-3 pb-6 text-[10px] font-semibold tracking-wider uppercase leading-relaxed">
        <div>
          <div>03 NGUYEN CONG TRU STREET, BINH THO WARD, THU DUC CITY</div>
          <div>ORDER@TRENDCOFFEE.VN | +84 90 123 4567</div>
          <div>WWW.TRENDCOFFEE.NET</div>
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-1 text-[11px]">
          <div>{isVi ? 'LOẠI ĐƠN' : 'ORDER TYPE'}: {orderTypeLabel(order_type, isVi)}</div>
          {order_type === 'at-table' && table_name && <div>{isVi ? 'BÀN' : 'TABLE'}: {table_name}</div>}
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
              <div
                key={line.line_id ?? `${line.name}-${index}`}
                className="grid grid-cols-12 items-start"
                data-cart-line={line.line_id ?? ''}
              >
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
            {isVi ? 'Giỏ hàng đang trống.' : 'Cart is empty.'}
          </div>
        )}
      </div>

      <div className="border-t-[1.5px] border-[#783820] pt-3 pb-6">
        <div className="ml-auto w-full max-w-64 space-y-1.5 text-[12px] font-semibold tracking-wider uppercase">
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</span>
            <span className="tabular-nums">{money(total)}</span>
          </div>
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'PHÍ DỊCH VỤ (10%)' : 'SERVICE CHARGE (10%)'}</span>
            <span className="tabular-nums">{money(0)}</span>
          </div>
          <div className="flex justify-between border-t border-[#783820] pt-2 text-[14px] font-bold">
            <span>{isVi ? 'TỔNG CỘNG (TẠM TÍNH)' : 'TOTAL ESTIMATED'}</span>
            <span className="text-[15px] font-extrabold tabular-nums">{money(total)}</span>
          </div>
        </div>
        <div className="mt-8 max-w-[440px] space-y-1 text-[10px] font-medium tracking-wider uppercase leading-relaxed">
          <div>{isVi ? 'THẺ TÍN DỤNG & GHI NỢ: VISA, MASTERCARD, NAPAS' : 'CREDIT & DEBIT CARDS: VISA, MASTERCARD, NAPAS'}</div>
          <div className="pt-2">{isVi ? 'CHUYỂN KHOẢN:' : 'BANK TRANSFER:'}</div>
          <div>{isVi ? 'TÊN TÀI KHOẢN: CONG TY CO PHAN TREND COFFEE' : 'ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE'}</div>
          <div>MB BANK: 9999.8888.68</div>
          <div className="pt-2">{isVi ? 'TRẠNG THÁI: ĐANG XỬ LÝ ĐƠN HÀNG (VUI LÒNG KIỂM TRA MÓN).' : 'STATUS: ORDER IN PROGRESS (PLEASE CHECK YOUR ITEMS).'}</div>
        </div>
      </div>

      {order_note && (
        <div className="border-t-[1.5px] border-[#783820] pt-4 text-[11px] leading-relaxed">
          <span className="font-bold tracking-wider uppercase">{isVi ? 'GHI CHÚ ĐƠN HÀNG: ' : 'ORDER NOTE: '}</span>
          <span>{order_note}</span>
        </div>
      )}

      <ReceiptFooter isVi={isVi} />
    </div>
  );
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
    <div className="mx-auto flex w-full max-w-[680px] flex-1 flex-col px-10 py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl">
          {isVi ? 'HÓA ĐƠN' : 'INVOICE'}
        </h1>
        <div className="text-right text-[11px] font-semibold tracking-wider uppercase leading-tight">
          <div>{isVi ? 'MÃ HÓA ĐƠN' : 'INVOICE NO.'}: {order_id}</div>
          <div>{isVi ? 'TRẠNG THÁI' : 'STATUS'}: {isVi && status.toLowerCase() === 'pending' ? 'CHỜ XỬ LÝ' : status.toUpperCase()}</div>
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
        <div className="ml-auto w-full max-w-64 space-y-1.5 text-[12px] font-semibold tracking-wider uppercase">
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</span>
            <span className="tabular-nums">{money(total)}</span>
          </div>
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'PHÍ DỊCH VỤ (10%)' : 'SERVICE CHARGE (10%)'}</span>
            <span className="tabular-nums">{money(0)}</span>
          </div>
          <div className="flex justify-between border-t border-[#783820] pt-2 text-[14px] font-bold">
            <span>{isVi ? 'TỔNG TIỀN THANH TOÁN' : 'TOTAL AMOUNT DUE'}</span>
            <span className="text-[15px] font-extrabold tabular-nums">{money(total)}</span>
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
}) {
  const isVi = uiLanguage === 'vi';
  const isImage = isSafeQrImageSource(qr_code);
  const statusLabel = status?.toLowerCase() === 'pending'
    ? (isVi ? 'CHỜ THANH TOÁN' : 'PENDING PAYMENT')
    : (status?.toUpperCase() ?? (isVi ? 'KHÔNG CÓ TRẠNG THÁI' : 'PAYMENT STATUS UNAVAILABLE'));

  return (
    <div className="mx-auto flex w-full max-w-[680px] flex-1 flex-col px-10 py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl">
          {isVi ? 'HÓA ĐƠN' : 'INVOICE'}
        </h1>
        <div className="text-right text-[11px] font-semibold tracking-wider uppercase leading-tight">
          <div>{isVi ? 'MÃ HÓA ĐƠN' : 'INVOICE NO.'}: {order_id}</div>
          <div>{isVi ? 'TRẠNG THÁI' : 'STATUS'}: {statusLabel}</div>
        </div>
      </div>

      <div className="space-y-2 pt-3 pb-6 text-[10px] font-semibold tracking-wider uppercase leading-relaxed">
        <div className="flex flex-wrap gap-x-8 gap-y-1 text-[11px]">
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

      <div className="grid grid-cols-12 border-b-[1.5px] border-[#783820] pb-1.5 text-[12px] font-bold tracking-widest uppercase">
        <div className="col-span-6">{isVi ? 'MÓN' : 'ITEM'}</div>
        <div className="col-span-2 text-center">{isVi ? 'SL' : 'QTY'}</div>
        <div className="col-span-2 text-right">{isVi ? 'ĐƠN GIÁ' : 'UNIT PRICE'}</div>
        <div className="col-span-2 text-right">{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</div>
      </div>

      <div className="space-y-2.5 py-3 text-[12.5px] font-medium tracking-wide">
        {lines.length > 0 ? lines.map((line, index) => {
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
        }) : (
          <div className="py-3 text-center text-[11px] font-semibold tracking-wider uppercase text-[#9b7352]">
            {isVi ? 'KHÔNG CÓ CHI TIẾT ĐƠN HÀNG.' : 'ORDER DETAILS UNAVAILABLE.'}
          </div>
        )}
      </div>

      <div className="border-t-[1.5px] border-[#783820] pt-3">
        <div className="ml-auto w-full max-w-64 space-y-1.5 font-semibold tracking-wider uppercase">
          {total !== undefined && (
            <div className="flex justify-between text-[10px]">
              <span>{isVi ? 'TẠM TÍNH' : 'SUBTOTAL'}</span>
              <span className="tabular-nums">{money(total)}</span>
            </div>
          )}
          <div className="flex justify-between text-[10px]">
            <span>{isVi ? 'PHÍ DỊCH VỤ (10%)' : 'SERVICE CHARGE (10%)'}</span>
            <span className="tabular-nums">{money(0)}</span>
          </div>
          <div className="flex justify-between border-t border-[#783820] pt-2 text-[14px] font-bold">
            <span>{isVi ? 'TỔNG TIỀN THANH TOÁN' : 'TOTAL AMOUNT DUE'}</span>
            <span className="text-[15px] font-extrabold tabular-nums">
              {total !== undefined ? money(total) : '—'}
            </span>
          </div>
        </div>

        <div className="mt-7 grid grid-cols-12 items-end gap-6">
          <div className="col-span-7 space-y-1 text-[9.5px] font-medium tracking-wider uppercase leading-relaxed">
            <div>{isVi ? 'THẺ TÍN DỤNG & GHI NỢ: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS' : 'CREDIT & DEBIT CARDS: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS'}</div>
            <div className="pt-2">{isVi ? 'CHUYỂN KHOẢN:' : 'BANK TRANSFER:'}</div>
            <div>{isVi ? 'TÊN TÀI KHOẢN: CONG TY CO PHAN TREND COFFEE' : 'ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE'}</div>
            <div>MB BANK: 9999.8888.68</div>
          </div>
          <div className="col-span-5 flex justify-end">
            {isImage ? (
              <img
                src={qr_code}
                alt="Verified payment QR code"
                className="h-36 w-36 object-contain mix-blend-multiply"
              />
            ) : (
              <div className="max-w-36 border border-[#c4ab91] px-3 py-5 text-center text-[9px] font-semibold tracking-wider uppercase">
                {isVi ? 'KHÔNG THỂ HIỂN THỊ MÃ QR XÁC THỰC.' : 'UNABLE TO DISPLAY A VERIFIED QR CODE.'}
              </div>
            )}
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
  const { language: storedLanguage } = useUiLanguage();
  const uiLanguage = forcedLanguage ?? storedLanguage;
  const isVi = uiLanguage === 'vi';
  const [searchParams] = useSearchParams();
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
    if (preview === 'waiting') return { view: 'waiting' };
    return waitingState;
  });
  const [menuSnapshot, setMenuSnapshot] = useState<MenuDisplayState | null>(null);
  const [cartSnapshot, setCartSnapshot] = useState<CartDisplayState | null>(null);
  const [manualTab, setManualTab] = useState<DockTab | null>(null);

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
    } else {
      setState(waitingState);
    }
    setManualTab(null);
  }, [sessionId, preview]);

  useEffect(() => {
    if (state.view === 'menu') setMenuSnapshot(state);
    if (state.view === 'cart') setCartSnapshot(state);
  }, [state]);

  const handleEvent = useCallback((event: AgentEvent) => {
    if (!sessionId) return;
    setManualTab(null);
    setState((current) => reduceCustomerDisplay(current, event, sessionId));
  }, [sessionId]);

  useAgentEvents(undefined, handleEvent, ['display_update'], sessionId);

  const activeTab = manualTab ?? dockTabForState(state);
  const visibleState = manualTab === 'menu' && menuSnapshot
    ? menuSnapshot
    : manualTab === 'cart'
      ? (cartSnapshot ?? emptyCartState)
      : state;
  const cartLines = state.view === 'cart' ? state.lines : (cartSnapshot?.lines ?? []);

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
                />
              </>
            )}

            {visibleState.view === 'cart' && (
              <CartView
                lines={visibleState.lines}
                total={visibleState.total}
                order_note={visibleState.order_note}
                order_type={visibleState.order_type}
                table_name={visibleState.table_name}
                uiLanguage={uiLanguage}
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

      <CustomerDock
        activeTab={activeTab}
        cartCount={cartQuantity(cartLines)}
        onTabChange={setManualTab}
        uiLanguage={uiLanguage}
      />

      {/* Bottom Editorial Footer */}
      <EditorialFooter showInfo={visibleState.view !== 'cart'} />
    </div>
  );
}
