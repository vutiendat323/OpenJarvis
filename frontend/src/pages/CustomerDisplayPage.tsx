import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { useAgentEvents, type AgentEvent } from '@/lib/useAgentEvents';
import {
  isSafeQrImageSource,
  reduceCustomerDisplay,
  waitingState,
  type CustomerDisplayLine,
  type CustomerDisplayState,
  type CustomerMenuItem,
} from './customerDisplayState';

const vnd = new Intl.NumberFormat('vi-VN');

function money(value: number | undefined): string {
  return `${vnd.format(value ?? 0)}đ`;
}

// Fallback high quality dish assets
const defaultDishImages = [
  'https://trendcoffee.net/assets/highlight_menu_2-DP6SYppA.webp',
  'https://trendcoffee.net/assets/highlight_menu_5-CTt7s1hO.webp',
  'https://trendcoffee.net/assets/highlight_menu_4-4H0ItU9q.webp',
  'https://trendcoffee.net/assets/news_article_3_2-BhZ3ccgg.webp',
  'https://trendcoffee.net/assets/news_article_2_2-Ci68oNOp.webp',
  'https://trendcoffee.net/assets/highlight_menu_3-U5KOrbP8.webp',
];

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
function VintageRibbon({ title }: { title: string }) {
  return (
    <div className="relative my-4 flex w-full items-center justify-center">
      <div
        className="h-1.5 flex-1"
        style={{
          backgroundImage: 'radial-gradient(circle, #8c6239 1.6px, transparent 1.6px)',
          backgroundSize: '10px 6px',
          backgroundRepeat: 'repeat-x',
          backgroundPosition: 'center',
        }}
      />
      <div
        className="relative z-10 flex h-8 w-[450px] max-w-[52%] items-center justify-center bg-[#8c6239] text-sm font-semibold tracking-[0.32em] text-white uppercase shadow-sm"
        style={{
          clipPath: 'polygon(0% 0%, 100% 0%, calc(100% - 18px) 50%, 100% 100%, 0% 100%, 18px 50%)',
        }}
      >
        {title}
      </div>
      <div
        className="h-1.5 flex-1"
        style={{
          backgroundImage: 'radial-gradient(circle, #8c6239 1.6px, transparent 1.6px)',
          backgroundSize: '10px 6px',
          backgroundRepeat: 'repeat-x',
          backgroundPosition: 'center',
        }}
      />
    </div>
  );
}

// Restaurant Editorial Header (Only on Menu View)
function RestaurantHeader() {
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
        <div className="mt-0.5 text-2xl font-bold tracking-[0.35em] uppercase leading-none sm:text-3xl text-[#8c6239]">MENU</div>
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
function EditorialFooter() {
  return (
    <footer className="mt-auto shrink-0 font-['Josefin_Sans',sans-serif]">
      <div className="flex flex-wrap items-center justify-center gap-6 bg-[#c0a386] px-8 py-2 text-[11px] font-medium tracking-wider text-white">
        <div>@trendcoffee.vn</div>
        <div>Trend Coffee Vietnam</div>
        <div>trendcoffee.net</div>
        <div>Số 03 Nguyễn Công Trứ, TP. Thủ Đức</div>
      </div>
      <StripedBand />
    </footer>
  );
}

interface CatalogCategory {
  slug: string;
  name: string;
  subtitle: string;
  image: string;
}

const trendCoffeeCategories: CatalogCategory[] = [
  {
    slug: 'd07665b001',
    name: 'Cà phê',
    subtitle: 'Đen, Sữa, Bạc xỉu, Cold Brew, Espresso',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/capheden-1772763183937',
  },
  {
    slug: 'a87d969ab5',
    name: 'Món trà',
    subtitle: 'Trà thảo mộc mật ong, Trà sen vàng thanh mát',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/trathaomocmatong-1772762966320',
  },
  {
    slug: 'e6cfb79d94',
    name: 'Sinh tố',
    subtitle: 'Sinh tố hoa quả nhiệt đới, Nước dừa tươi',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/nuocdua-1772762836291',
  },
  {
    slug: '12f10617d2',
    name: 'Nước giải khát',
    subtitle: 'Coca-Cola, soda đóng chai mát lạnh',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/coca-1781090606277.jfif',
  },
  {
    slug: '29e0552928',
    name: 'Món bánh',
    subtitle: 'Croissant Ham & Cheese, Tiramisu, Panna Cotta',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/banhlanh-1772762278613',
  },
  {
    slug: '24dffe01f6',
    name: 'Món ăn',
    subtitle: 'Súp trong ngày, Khoai tây chiên, Burger & Pasta',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/xoup_bi_do-1781776637091.avif',
  },
  {
    slug: 'd60c1f2946',
    name: 'Bia/ rượu vang',
    subtitle: 'Corona Extra, bia Bỉ, rượu vang cao cấp',
    image: 'https://trendcoffee.s3.ap-southeast-1.amazonaws.com/corona_extra-1781751048582.jpeg',
  },
  {
    slug: '8c25bf5866',
    name: 'Giá tùy chỉnh',
    subtitle: 'Combo ưu đãi, thực đơn theo yêu cầu riêng',
    image: 'https://trendcoffee.net/assets/highlight_menu_2-DP6SYppA.webp',
  },
];

// VIEW 1: APPROVED MENU
function MenuView({ items }: { items: CustomerMenuItem[] }) {
  const mainDishes = items.length > 0 ? items.slice(0, 6) : [
    { name: 'CÀ PHÊ SỮA', price: 40000, note: 'Cà phê pha phin truyền thống thơm nồng kết hợp sữa đặc ngọt ngào.' },
    { name: 'CÀ PHÊ BẠC XỈU', price: 48000, note: 'Hòa quyện giữa nhiều sữa béo ấm và một chút hương cà phê nhẹ nhàng.' },
    { name: 'SÚP TRONG NGÀY', price: 64000, note: 'Súp bí đỏ kem tươi béo ngậy nấu theo công thức đặc biệt mỗi ngày.' },
    { name: 'KHOAI TÂY CHIÊN', price: 75000, note: 'Khoai tây giòn rụm chấm cùng xốt tương ớt và mayonnaise đặc trưng.' },
    { name: 'BÁNH CROISSANT HAM & CHEESE', price: 49000, note: 'Bánh sừng bò nướng nóng giòn kẹp thịt nguội và phô mai tan chảy.' },
    { name: 'BÁNH PANNA COTTA', price: 64000, note: 'Món tráng miệng Ý mềm mịn thanh mát kết hợp xốt dâu thơm dịu.' },
  ];

  return (
    <div className="flex flex-1 flex-col px-8 pb-6 text-[#8c6239]">
      <VintageRibbon title={items.length > 0 ? "RECOMMENDATIONS" : "SIGNATURE DISHES"} />
      <div className="mb-6 grid grid-cols-1 gap-6 md:grid-cols-3">
        {mainDishes.slice(0, 3).map((item, index) => (
          <div key={item.id ?? `${item.name}-${index}`} className="flex flex-col">
            <div className="mb-2 h-40 w-full overflow-hidden rounded-[4px] border border-[#d9c2a7] bg-white/70 p-2 shadow-[0_2px_5px_rgba(110,71,38,0.12)] flex items-center justify-center">
              <img
                src={item.image_url || defaultDishImages[index % defaultDishImages.length]}
                alt={item.name}
                className="max-h-full max-w-full object-contain transition-transform duration-300 hover:scale-105"
                onError={(e) => {
                  e.currentTarget.src = defaultDishImages[index % defaultDishImages.length];
                }}
              />
            </div>
            <div className="flex items-center justify-between font-bold text-[12.5px] tracking-[0.12em] uppercase">
              <span>{item.name}</span>
              <span className="tabular-nums">{item.price !== undefined ? (item.price >= 1000 ? Math.round(item.price / 1000) : item.price) : '10'}</span>
            </div>
            <p className="mt-0.5 font-['Cormorant_Garamond',serif] text-[14px] italic leading-tight text-[#9b7352]">
              {item.note || 'Special gourmet recipe prepared fresh daily with premium ingredients.'}
            </p>
          </div>
        ))}
      </div>

      {mainDishes.length > 3 && (
        <div className="mb-4 grid grid-cols-1 gap-x-6 gap-y-3.5 pb-2 text-[12px] md:grid-cols-3">
          {mainDishes.slice(3).map((item, index) => (
            <div key={item.id ?? `${item.name}-${index + 3}`}>
              <div className="flex items-center justify-between font-bold text-[12px] tracking-[0.12em] uppercase">
                <span>{item.name}</span>
                <span className="tabular-nums">{item.price !== undefined ? (item.price >= 1000 ? Math.round(item.price / 1000) : item.price) : '15'}</span>
              </div>
              <p className="font-['Cormorant_Garamond',serif] text-[13.5px] italic leading-tight text-[#9b7352]">
                {item.note || 'Delicate blend of herbs and chef signature sauce.'}
              </p>
            </div>
          ))}
        </div>
      )}

      <VintageRibbon title="CATEGORIES" />
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-2 md:grid-cols-4 md:gap-5">
        {trendCoffeeCategories.map((cat, index) => (
          <div key={cat.slug} className="group flex flex-col transition-transform duration-200 hover:-translate-y-0.5">
            <div className="mb-2 aspect-square w-full overflow-hidden rounded-[4px] border border-[#d9c2a7] bg-white/70 p-2.5 shadow-[0_2px_5px_rgba(110,71,38,0.12)] flex items-center justify-center">
              <img
                src={cat.image}
                alt={cat.name}
                className="max-h-full max-w-full object-contain transition-transform duration-300 group-hover:scale-105"
                onError={(e) => {
                  e.currentTarget.src = defaultDishImages[index % defaultDishImages.length];
                }}
              />
            </div>
            <div className="flex items-center justify-between font-bold text-[12px] tracking-[0.12em] uppercase text-[#8c6239]">
              <span>{cat.name}</span>
            </div>
            <p className="mt-0.5 font-['Cormorant_Garamond',serif] text-[13px] italic leading-tight text-[#9b7352]">
              {cat.subtitle}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// VIEW 2: CART (ORDER SLIP LUXURY INVOICE)
function CartView({ lines, total }: { lines: CustomerDisplayLine[]; total: number }) {
  return (
    <div className="mx-auto w-full max-w-[680px] flex-1 px-10 py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl">
          ORDER SLIP
        </h1>
        <div className="text-right text-[11px] font-semibold tracking-wider uppercase leading-tight">
          <div>ORDER NO.: TC-000245</div>
          <div>DATE OF ISSUE: AUGUST 28, 2026</div>
        </div>
      </div>

      <div className="pt-3 pb-6 text-[11px] font-medium tracking-wider uppercase leading-relaxed">
        <div>SỐ 03 NGUYỄN CÔNG TRỨ, PHƯỜNG BÌNH THỌ, TP. THỦ ĐỨC</div>
        <div>ORDER@TRENDCOFFEE.VN | +84 90 123 4567</div>
        <div>WWW.TRENDCOFFEE.NET</div>
        <div>ORDER CHANNEL: COUNTER #01</div>
      </div>

      <div className="grid grid-cols-12 border-b-[1.5px] border-[#783820] pb-1.5 text-[12px] font-bold tracking-widest uppercase">
        <div className="col-span-6">ITEM</div>
        <div className="col-span-2 text-center">QTY</div>
        <div className="col-span-2 text-right">UNIT PRICE</div>
        <div className="col-span-2 text-right">SUBTOTAL</div>
      </div>

      <div className="space-y-2.5 py-3 text-[12.5px] font-medium tracking-wide">
        {lines.length > 0 ? (
          lines.map((line, index) => {
            const qty = line.quantity ?? 1;
            const lineTotal = line.line_total ?? (line.quantity ? line.quantity * 35000 : 35000);
            const unitPrice = qty > 0 ? Math.round(lineTotal / qty) : lineTotal;
            return (
              <div key={`${line.name}-${index}`} className="grid grid-cols-12 items-center">
                <div className="col-span-6 font-semibold uppercase">
                  “{line.name}”{line.size ? ` (${line.size})` : ''}
                </div>
                <div className="col-span-2 text-center font-normal">{qty}</div>
                <div className="col-span-2 text-right tabular-nums">{money(unitPrice)}</div>
                <div className="col-span-2 text-right font-semibold tabular-nums">{money(lineTotal)}</div>
              </div>
            );
          })
        ) : (
          <>
            <div className="grid grid-cols-12 items-center">
              <div className="col-span-6 font-semibold uppercase">“TRUYỀN THỐNG” CÀ PHÊ PHIN</div>
              <div className="col-span-2 text-center font-normal">2</div>
              <div className="col-span-2 text-right tabular-nums">29.000đ</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">58.000đ</div>
            </div>
            <div className="grid grid-cols-12 items-center">
              <div className="col-span-6 font-semibold uppercase">TRÀ ĐÀO CAM SẢ ĐẶC BIỆT</div>
              <div className="col-span-2 text-center font-normal">1</div>
              <div className="col-span-2 text-right tabular-nums">45.000đ</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">45.000đ</div>
            </div>
            <div className="grid grid-cols-12 items-center">
              <div className="col-span-6 font-semibold uppercase">BÁNH CROISSANT BƠ PHÁP</div>
              <div className="col-span-2 text-center font-normal">1</div>
              <div className="col-span-2 text-right tabular-nums">35.000đ</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">35.000đ</div>
            </div>
          </>
        )}
      </div>

      <div className="flex justify-end border-t-[1.5px] border-[#783820] pt-3 pb-6">
        <div className="w-64 space-y-1.5 text-[12px] font-semibold tracking-wider uppercase">
          <div className="flex justify-between">
            <span>SUBTOTAL</span>
            <span className="tabular-nums">{money(total)}</span>
          </div>
          <div className="flex justify-between">
            <span>SERVICE CHARGE (0%)</span>
            <span className="tabular-nums">0đ</span>
          </div>
          <div className="flex justify-between pt-1 text-[14px] font-bold">
            <span>TOTAL ESTIMATED</span>
            <span className="text-[15px] font-extrabold tabular-nums">{money(total)}</span>
          </div>
        </div>
      </div>

      <div className="space-y-2 pt-4 text-[10.5px] font-medium tracking-wider uppercase leading-relaxed">
        <div>CREDIT &amp; DEBIT CARDS: VISA, MASTERCARD, NAPAS</div>
        <div>
          BANK TRANSFER:<br />
          ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE<br />
          MB BANK: 9999.8888.68
        </div>
        <div>STATUS: ORDER IN PROGRESS (QUÝ KHÁCH VUI LÒNG KIỂM TRA MÓN).</div>
      </div>

      <div className="mt-8 flex items-center justify-between border-t-[1.5px] border-[#783820] pt-3">
        <div className="flex items-center gap-3">
          <svg className="h-8 w-8 text-[#783820]" viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth="3">
            <polygon points="50,10 90,85 10,85" />
            <line x1="50" y1="10" x2="30" y2="85" />
            <line x1="50" y1="10" x2="70" y2="85" />
            <line x1="50" y1="10" x2="50" y2="85" />
          </svg>
          <div className="text-left leading-tight">
            <div className="text-sm font-bold tracking-[0.2em] uppercase">TREND</div>
            <div className="text-[9.5px] font-medium tracking-[0.25em] uppercase">COFFEE &amp; RESTAURANT</div>
          </div>
        </div>
        <div className="font-['Playfair_Display',serif] text-sm tracking-wider uppercase sm:text-base text-right">
          THANK YOU FOR DINING WITH US.
        </div>
      </div>
    </div>
  );
}

// VIEW 3: BILL (INVOICE LUXURY INVOICE)
function BillView({
  order_id,
  branch,
  status,
  lines,
  total,
}: {
  order_id: string;
  branch: string;
  order_type: string;
  status: string;
  lines: CustomerDisplayLine[];
  total: number;
}) {
  return (
    <div className="mx-auto w-full max-w-[680px] flex-1 px-10 py-8 text-left font-['Josefin_Sans',sans-serif] text-[#783820]">
      <div className="flex items-baseline justify-between border-b-[1.5px] border-[#783820] pb-2">
        <h1 className="font-['Playfair_Display',serif] text-4xl font-normal tracking-wide uppercase sm:text-5xl">
          INVOICE
        </h1>
        <div className="text-right text-[11px] font-semibold tracking-wider uppercase leading-tight">
          <div>INVOICE NO.: {order_id || 'INV-000245'}</div>
          <div>STATUS: {status.toUpperCase()}</div>
        </div>
      </div>

      <div className="pt-3 pb-6 text-[11px] font-medium tracking-wider uppercase leading-relaxed">
        <div>{branch || 'SỐ 03 NGUYỄN CÔNG TRỨ, PHƯỜNG BÌNH THỌ, TP. THỦ ĐỨC'}</div>
        <div>RESERVATIONS@TRENDCOFFEE.VN | +84 90 123 4567</div>
        <div>WWW.TRENDCOFFEE.NET</div>
        <div>MST / CUIT: 30-71234567-8</div>
      </div>

      <div className="grid grid-cols-12 border-b-[1.5px] border-[#783820] pb-1.5 text-[12px] font-bold tracking-widest uppercase">
        <div className="col-span-6">ITEM</div>
        <div className="col-span-2 text-center">QTY</div>
        <div className="col-span-2 text-right">UNIT PRICE</div>
        <div className="col-span-2 text-right">SUBTOTAL</div>
      </div>

      <div className="space-y-2.5 py-3 text-[12.5px] font-medium tracking-wide">
        {lines.length > 0 ? (
          lines.map((line, index) => {
            const qty = line.quantity ?? 1;
            const lineTotal = line.line_total ?? (line.quantity ? line.quantity * 35000 : 35000);
            const unitPrice = qty > 0 ? Math.round(lineTotal / qty) : lineTotal;
            return (
              <div key={`${line.name}-${index}`} className="grid grid-cols-12 items-center">
                <div className="col-span-6 font-semibold uppercase">
                  “{line.name}”{line.size ? ` (${line.size})` : ''}
                </div>
                <div className="col-span-2 text-center font-normal">{qty}</div>
                <div className="col-span-2 text-right tabular-nums">{money(unitPrice)}</div>
                <div className="col-span-2 text-right font-semibold tabular-nums">{money(lineTotal)}</div>
              </div>
            );
          })
        ) : (
          <>
            <div className="grid grid-cols-12 items-center">
              <div className="col-span-6 font-semibold uppercase">“LA NUIT” TASTING MENU (COMBO)</div>
              <div className="col-span-2 text-center font-normal">1</div>
              <div className="col-span-2 text-right tabular-nums">58.000đ</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">58.000đ</div>
            </div>
            <div className="grid grid-cols-12 items-center">
              <div className="col-span-6 font-semibold uppercase">TRÀ ĐÀO CAM SẢ ĐẶC BIỆT</div>
              <div className="col-span-2 text-center font-normal">1</div>
              <div className="col-span-2 text-right tabular-nums">45.000đ</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">45.000đ</div>
            </div>
            <div className="grid grid-cols-12 items-center">
              <div className="col-span-6 font-semibold uppercase">BÁNH CROISSANT BƠ PHÁP</div>
              <div className="col-span-2 text-center font-normal">1</div>
              <div className="col-span-2 text-right tabular-nums">35.000đ</div>
              <div className="col-span-2 text-right font-semibold tabular-nums">35.000đ</div>
            </div>
          </>
        )}
      </div>

      <div className="flex justify-end border-t-[1.5px] border-[#783820] pt-3 pb-6">
        <div className="w-64 space-y-1.5 text-[12px] font-semibold tracking-wider uppercase">
          <div className="flex justify-between">
            <span>SUBTOTAL</span>
            <span className="tabular-nums">{money(total)}</span>
          </div>
          <div className="flex justify-between">
            <span>SERVICE CHARGE (10%)</span>
            <span className="tabular-nums">0đ</span>
          </div>
          <div className="flex justify-between pt-1 text-[14px] font-bold">
            <span>TOTAL AMOUNT DUE</span>
            <span className="text-[15px] font-extrabold tabular-nums">{money(total)}</span>
          </div>
        </div>
      </div>

      <div className="space-y-2 pt-4 text-[10.5px] font-medium tracking-wider uppercase leading-relaxed">
        <div>CREDIT &amp; DEBIT CARDS: VISA, MASTERCARD, AMERICAN EXPRESS</div>
        <div>
          BANK TRANSFER:<br />
          ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE<br />
          MB BANK: 9999.8888.68<br />
          MST: 30-71234567-8
        </div>
        <div>CASH PAYMENTS: IN-PERSON ONLY.</div>
      </div>

      <div className="mt-8 flex items-center justify-between border-t-[1.5px] border-[#783820] pt-3">
        <div className="flex items-center gap-3">
          <svg className="h-8 w-8 text-[#783820]" viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth="3">
            <polygon points="50,10 90,85 10,85" />
            <line x1="50" y1="10" x2="30" y2="85" />
            <line x1="50" y1="10" x2="70" y2="85" />
            <line x1="50" y1="10" x2="50" y2="85" />
          </svg>
          <div className="text-left leading-tight">
            <div className="text-sm font-bold tracking-[0.2em] uppercase">TREND</div>
            <div className="text-[9.5px] font-medium tracking-[0.25em] uppercase">COFFEE &amp; RESTAURANT</div>
          </div>
        </div>
        <div className="font-['Playfair_Display',serif] text-sm tracking-wider uppercase sm:text-base text-right">
          THANK YOU FOR DINING WITH US.
        </div>
      </div>
    </div>
  );
}

// VIEW 4: PAYMENT QR (MINIMALIST SAMPLE)
function PaymentQrView({
  qr_code,
  total,
  order_id,
}: {
  qr_code: string;
  total?: number;
  order_id: string;
}) {
  const isImage = isSafeQrImageSource(qr_code);

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-8 py-10 text-center font-['Josefin_Sans',sans-serif]">
      <div className="mb-4 -rotate-2 font-['Alex_Brush',cursive] text-6xl leading-none text-[#6e2b14] sm:text-7xl">
        Payment
      </div>

      <div className="mb-4 inline-block overflow-hidden rounded-2xl border border-[#c4ab91] bg-white p-1 shadow-xl">
        {isImage ? (
          <div className="overflow-hidden rounded-xl">
            <img
              src={qr_code}
              alt="Mã thanh toán QR"
              className="mx-auto h-64 w-64 scale-112 object-contain sm:h-72 sm:w-72"
            />
          </div>
        ) : (
          <svg className="mx-auto h-64 w-64 sm:h-72 sm:w-72" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect width="120" height="120" fill="white" />
            <rect x="8" y="8" width="30" height="30" rx="3" fill="#6e2b14" />
            <rect x="13" y="13" width="20" height="20" rx="2" fill="white" />
            <rect x="17" y="17" width="12" height="12" rx="1.5" fill="#6e2b14" />

            <rect x="82" y="8" width="30" height="30" rx="3" fill="#6e2b14" />
            <rect x="87" y="13" width="20" height="20" rx="2" fill="white" />
            <rect x="91" y="17" width="12" height="12" rx="1.5" fill="#6e2b14" />

            <rect x="8" y="82" width="30" height="30" rx="3" fill="#6e2b14" />
            <rect x="13" y="87" width="20" height="20" rx="2" fill="white" />
            <rect x="17" y="91" width="12" height="12" rx="1.5" fill="#6e2b14" />

            <rect x="44" y="10" width="5" height="5" fill="#6e2b14" />
            <rect x="54" y="10" width="10" height="5" fill="#6e2b14" />
            <rect x="70" y="10" width="5" height="5" fill="#6e2b14" />
            <rect x="48" y="18" width="8" height="5" fill="#6e2b14" />
            <rect x="62" y="18" width="12" height="5" fill="#6e2b14" />
            <rect x="44" y="26" width="6" height="6" fill="#6e2b14" />
            <rect x="56" y="26" width="6" height="6" fill="#6e2b14" />
            <rect x="68" y="26" width="6" height="6" fill="#6e2b14" />

            <rect x="46" y="46" width="28" height="28" rx="2" fill="#6e2b14" />
            <rect x="52" y="52" width="16" height="16" rx="1" fill="white" />
            <rect x="56" y="56" width="8" height="8" fill="#6e2b14" />

            <rect x="10" y="44" width="8" height="8" fill="#6e2b14" />
            <rect x="24" y="44" width="14" height="6" fill="#6e2b14" />
            <rect x="12" y="56" width="6" height="14" fill="#6e2b14" />
            <rect x="24" y="64" width="12" height="8" fill="#6e2b14" />

            <rect x="80" y="44" width="10" height="6" fill="#6e2b14" />
            <rect x="96" y="44" width="14" height="8" fill="#6e2b14" />
            <rect x="84" y="56" width="14" height="8" fill="#6e2b14" />
            <rect x="102" y="58" width="10" height="14" fill="#6e2b14" />
            <rect x="80" y="68" width="8" height="6" fill="#6e2b14" />
            <rect x="94" y="68" width="18" height="6" fill="#6e2b14" />

            <rect x="44" y="82" width="12" height="6" fill="#6e2b14" />
            <rect x="62" y="82" width="8" height="6" fill="#6e2b14" />
            <rect x="76" y="82" width="14" height="6" fill="#6e2b14" />
            <rect x="96" y="82" width="14" height="6" fill="#6e2b14" />

            <rect x="46" y="94" width="8" height="8" fill="#6e2b14" />
            <rect x="60" y="94" width="18" height="8" fill="#6e2b14" />
            <rect x="84" y="94" width="8" height="16" fill="#6e2b14" />
            <rect x="98" y="94" width="14" height="8" fill="#6e2b14" />

            <rect x="44" y="106" width="16" height="6" fill="#6e2b14" />
            <rect x="66" y="106" width="12" height="6" fill="#6e2b14" />
            <rect x="98" y="106" width="14" height="6" fill="#6e2b14" />
          </svg>
        )}
      </div>

      <div className="mb-1 text-base font-medium tracking-[0.2em] uppercase text-[#6e2b14] sm:text-lg">
        Scan Now
      </div>

      <div className="mt-2 space-y-0.5 text-xs text-[#8c6239]">
        {total !== undefined && total > 0 && (
          <div className="text-lg font-bold tabular-nums text-[#6e2b14]">
            {money(total)}
          </div>
        )}
        <div className="text-[11px] opacity-85">
          MB Bank &bull; STK: <strong>9999.8888.68</strong> &bull; CONG TY CO PHAN TREND COFFEE
        </div>
        {order_id && (
          <div className="text-[10px] text-[#9b7352] opacity-75">
            Mã đơn hàng: {order_id}
          </div>
        )}
      </div>
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
                Khởi đầu ngày mới tràn đầy năng lượng cùng hương vị cà phê phin mộc thơm nồng nàn và bánh sừng bò giòn tan chuẩn vị Pháp.
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

export function CustomerDisplayPage() {
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session')?.trim() || undefined;
  const preview = searchParams.get('preview')?.trim();
  const [state, setState] = useState<CustomerDisplayState>(() => {
    if (preview === 'menu') return { view: 'menu', items: [] };
    if (preview === 'waiting') return { view: 'waiting' };
    return waitingState;
  });

  useEffect(() => {
    if (preview === 'menu') {
      setState({ view: 'menu', items: [] });
    } else {
      setState(waitingState);
    }
  }, [sessionId, preview]);

  const handleEvent = useCallback((event: AgentEvent) => {
    if (!sessionId) return;
    setState((current) => reduceCustomerDisplay(current, event, sessionId));
  }, [sessionId]);

  useAgentEvents(undefined, handleEvent, ['display_update'], sessionId);

  return (
    <div className="min-h-screen w-full bg-[#fae7cd] text-[#8c6239] font-['Josefin_Sans',sans-serif] flex flex-col justify-between overflow-x-hidden selection:bg-[#8c6239] selection:text-white">
      {/* Top Striped Band on Full Page */}
      <StripedBand />

      {/* Main Container */}
      <main className="w-full flex-1 flex flex-col justify-between max-w-[1024px] mx-auto py-2">
        {!sessionId && !preview ? (
          <div className="my-auto flex flex-col items-center justify-center p-8 text-center">
            <h2 className="font-['Alex_Brush',cursive] text-5xl text-[#8c6239] mb-2">Trend Coffee</h2>
            <p className="text-sm font-semibold tracking-widest uppercase text-[#9b7352]">
              Sẵn sàng phục vụ &bull; Đang chờ kết nối phiên hiển thị
            </p>
          </div>
        ) : (
          <>
            {state.view === 'menu' && (
              <>
                <RestaurantHeader />
                <MenuView items={state.items} />
              </>
            )}

            {state.view === 'cart' && (
              <CartView lines={state.lines} total={state.total} />
            )}

            {state.view === 'bill' && (
              <BillView
                order_id={state.order_id}
                branch={state.branch}
                order_type={state.order_type}
                status={state.status}
                lines={state.lines}
                total={state.total}
              />
            )}

            {state.view === 'payment_qr' && (
              <PaymentQrView
                qr_code={state.qr_code}
                total={state.total}
                order_id={state.order_id}
              />
            )}

            {state.view === 'waiting' && (
              <WaitingView />
            )}
          </>
        )}
      </main>

      {/* Bottom Editorial Footer */}
      <EditorialFooter />
    </div>
  );
}
