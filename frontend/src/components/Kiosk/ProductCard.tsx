import { useEffect, useState, type CSSProperties } from 'react';

import type { UiLanguage } from '@/hooks/useUiLanguage';
import type { TouchCartOrder } from '@/lib/kioskPresentation';
import { menuItemPortions, type CustomerMenuItem, type CustomerMenuVariant } from '@/pages/customerDisplayState';

export type ProductCardIntent = 'add' | 'buy';

export interface ProductCardProps {
  item: CustomerMenuItem;
  uiLanguage?: UiLanguage;
  onClose: () => void;
  /** Resolves once the draft cart holds the line; rejects with the server reason. */
  onSubmit: (order: TouchCartOrder, intent: ProductCardIntent) => Promise<void>;
}

const MAX_QUANTITY = 99;
const vnd = new Intl.NumberFormat('en-US');

const TEXT = {
  en: {
    portion: 'Portion',
    standard: 'Standard',
    quantity: 'Quantity',
    subtotal: 'Subtotal',
    note: 'Note',
    notePlaceholder: 'Add a note for the kitchen…',
    add: 'Add to cart',
    buy: 'Buy now',
    close: 'Close',
    soldOut: 'Sold out',
    failed: 'Could not add this item. Please try again.',
  },
  vi: {
    portion: 'Khẩu phần',
    standard: 'Tiêu chuẩn',
    quantity: 'Số lượng',
    subtotal: 'Tạm tính',
    note: 'Ghi chú',
    notePlaceholder: 'Ghi chú cho quầy pha chế…',
    add: 'Thêm vào giỏ',
    buy: 'Mua ngay',
    close: 'Đóng',
    soldOut: 'Hết món',
    failed: 'Chưa thêm được món, vui lòng thử lại.',
  },
} as const;

const REFUSALS: Record<string, Record<UiLanguage, string>> = {
  voice_session_required: {
    en: 'Start a chat with the assistant to order.',
    vi: 'Hãy bắt đầu trò chuyện với trợ lý để đặt món.',
  },
  menu_item_unavailable: { en: 'This item just sold out.', vi: 'Món này vừa hết.' },
  menu_item_not_found: {
    en: 'The menu just changed. Please pick the item again.',
    vi: 'Thực đơn vừa cập nhật, vui lòng chọn lại món.',
  },
  checkout_in_progress: {
    en: 'Your order is being paid. Please wait a moment.',
    vi: 'Đơn đang được thanh toán, vui lòng chờ một chút.',
  },
};

// Artisanal handmade cream paper texture: organic pulp lighting, gentle warm wash,
// and a soft natural top-left paper fold matching the artisanal sample ticket.
const PAPER_TEXTURE_SVG = "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='500' height='500'><filter id='p' x='0' y='0' width='100%25' height='100%25'><feTurbulence type='fractalNoise' baseFrequency='0.035' numOctaves='4' result='noise'/><feDiffuseLighting in='noise' lighting-color='%23fefcf7' surfaceScale='1.3' result='light'><feDistantLight azimuth='45' elevation='55'/></feDiffuseLighting><feColorMatrix type='matrix' values='0.82 0 0 0 0.16 0 0.78 0 0 0.15 0 0 0 0.72 0 0.13 0 0 0 1 0'/></filter><rect width='100%25' height='100%25' filter='url(%23p)'/></svg>\")";

const paperStyle: CSSProperties = {
  backgroundColor: '#f4ede2',
  backgroundImage: [
    'linear-gradient(135deg, transparent 0 46px, rgba(255,255,255,0.75) 47px, rgba(140,105,75,0.22) 49px, rgba(140,105,75,0.06) 53px, transparent 60px)',
    PAPER_TEXTURE_SVG,
    'radial-gradient(ellipse at 40% 25%, #fbf8f2 0%, #f4ece2 55%, #eae2d4 100%)',
  ].join(','),
  backgroundBlendMode: 'normal, multiply, normal',
  backgroundSize: 'auto, 400px 400px, cover',
  boxShadow: 'inset 0 1px 2px rgba(255,255,255,0.95), inset 0 0 0 1px rgba(180,145,110,0.28), 0 25px 50px -12px rgba(35,16,8,0.5), 0 4px 12px rgba(35,16,8,0.18)',
};

function PhotoFallback({ name }: { name: string }) {
  return (
    <div
      data-testid="product-card-photo-fallback"
      className="absolute inset-0 flex flex-col items-center justify-center gap-3.5 bg-[radial-gradient(circle_at_50%_40%,#f6ecdc,#e5d3ba)]"
    >
      <span className="font-['Fraunces',serif] text-[94px] font-bold leading-[0.8] text-[#68341a]/25" aria-hidden="true">
        {name.trim().charAt(0)}
      </span>
      <svg width="90" height="64" viewBox="0 0 120 84" fill="none" stroke="rgba(104,52,26,.45)" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <path d="M14 64a46 46 0 0192 0M6 64h108M20 72h80M60 18v4M38 34c4-4 9-7 14-8" />
        <circle cx="60" cy="14" r="4" />
      </svg>
      <span className="font-['Montserrat',sans-serif] text-[11px] font-bold uppercase tracking-[0.3em] text-[#967053]">
        Trend · Coffee
      </span>
    </div>
  );
}

export function ProductCard({ item, uiLanguage = 'en', onClose, onSubmit }: ProductCardProps) {
  const t = TEXT[uiLanguage];
  const rawPortions = menuItemPortions(item);
  const portions: CustomerMenuVariant[] = rawPortions.length > 0
    ? rawPortions
    : item.price !== undefined
      ? [{ id: item.id || 'default', size: '', price: item.price }]
      : [];
  const [portionId, setPortionId] = useState(portions[0]?.id ?? '');
  const [quantity, setQuantity] = useState(1);
  const [note, setNote] = useState('');
  const [photoFailed, setPhotoFailed] = useState(false);
  const [pending, setPending] = useState<ProductCardIntent | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const portion = portions.find((row) => row.id === portionId) ?? portions[0];
  const soldOut = item.available === false;
  const orderable = !soldOut && portion !== undefined && pending === null;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const submit = async (intent: ProductCardIntent) => {
    if (!orderable || portion === undefined) return;
    setPending(intent);
    setRefusal(null);
    try {
      // On success the page closes the card, so there is nothing to reset.
      await onSubmit({ variant_id: portion.id, quantity, note: note.trim() }, intent);
    } catch (error) {
      const reason = error instanceof Error ? error.message : '';
      setRefusal(REFUSALS[reason]?.[uiLanguage] ?? t.failed);
      setPending(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-[#200e06]/45 px-4 pb-[175px] pt-5 backdrop-blur-[4px] sm:px-6"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={item.name}
        data-testid="product-card"
        style={paperStyle}
        onClick={(event) => event.stopPropagation()}
        className="relative max-h-full w-full max-w-[820px] overflow-y-auto rounded-[24px] p-5 text-[#4a2c1d] sm:p-7"
      >
        <button
          type="button"
          data-testid="product-card-close"
          aria-label={t.close}
          onClick={onClose}
          className="absolute right-3.5 top-3.5 z-10 grid h-9 w-9 place-items-center rounded-full text-[#5c321a] transition-colors hover:bg-[#5c321a]/10 active:bg-[#5c321a]/15"
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>

        <div className="grid grid-cols-1 items-stretch gap-6 sm:grid-cols-[300px_1fr] sm:gap-7 lg:grid-cols-[320px_1fr]">
          <div className="relative min-h-[250px] w-full overflow-hidden rounded-[18px] bg-[#e8d7c2] shadow-[inset_0_0_0_1px_rgba(120,78,40,.15),0_2px_8px_rgba(60,30,15,.12)] sm:min-h-[350px]">
            {item.image_url && !photoFailed ? (
              <img
                src={item.image_url}
                alt={item.name}
                onError={() => setPhotoFailed(true)}
                className="absolute inset-0 h-full w-full object-cover object-[50%_50%]"
              />
            ) : (
              <PhotoFallback name={item.name} />
            )}
          </div>

          <div className="flex min-w-0 flex-col justify-between pt-0.5">
            <div>
              {(item.is_top_sell || item.is_new || soldOut) && (
                <div className="mb-2 flex flex-wrap items-center gap-2 pr-8 font-['Montserrat',sans-serif] text-[12px] font-bold uppercase tracking-[0.22em] text-[#b86a3d]">
                  {item.is_top_sell && <span className="rounded-full border border-[#b86a3d]/50 px-2.5 pb-[2px] pt-0.5">Top seller</span>}
                  {item.is_new && <span className="rounded-full border border-[#b86a3d]/50 px-2.5 pb-[2px] pt-0.5">New</span>}
                  {soldOut && <span className="rounded-full bg-[#5c2912] px-2.5 pb-[2px] pt-0.5 text-[#fae7cd]">{t.soldOut}</span>}
                </div>
              )}

              <h2 className="pr-6 font-['Fraunces',serif] text-[28px] font-bold leading-[1.12] tracking-tight text-[#3a1d0f] sm:text-[32px]">
                {item.name}
              </h2>

              {item.note && (
                <p className="mt-2 font-['Montserrat',sans-serif] text-[13px] leading-[1.5] text-[#634735] sm:text-[13.5px]">
                  {item.note}
                </p>
              )}

              {portions.length <= 1 ? (
                portion && (
                  <div className="mt-3.5 font-['Fraunces',serif] text-[19px] font-bold uppercase tracking-wider text-[#3d2010] sm:text-[21px]">
                    {(portion.size || t.standard).toUpperCase()} - {vnd.format(portion.price)} VND
                  </div>
                )
              ) : (
                <div className="mt-3.5 flex flex-wrap items-baseline gap-3.5" role="radiogroup" aria-label={t.portion}>
                  {portions.map((row) => {
                    const isSelected = row.id === portion?.id;
                    const label = `${(row.size || t.standard).toUpperCase()} - ${vnd.format(row.price)} VND`;
                    return (
                      <button
                        key={row.id}
                        type="button"
                        role="radio"
                        aria-checked={isSelected}
                        data-testid={`portion-${row.id}`}
                        onClick={() => setPortionId(row.id)}
                        className={`font-['Fraunces',serif] text-[17px] uppercase tracking-wide transition-all active:opacity-70 sm:text-[18px] ${
                          isSelected
                            ? 'font-bold text-[#3d2010] underline decoration-[#3d2010] decoration-2 underline-offset-4'
                            : 'font-normal text-[#8c6b54] hover:text-[#3d2010]'
                        }`}
                      >
                        {label}
                      </button>
                    );
                  })}
                </div>
              )}

              <div className="mt-3.5 flex items-center gap-2" aria-label={t.quantity}>
                <button
                  type="button"
                  data-testid="product-card-decrease"
                  aria-label="−"
                  disabled={quantity <= 1}
                  onClick={() => setQuantity((value) => Math.max(1, value - 1))}
                  className="grid h-9 w-9 place-items-center rounded-full border-[1.5px] border-[#5c321a] text-[#5c321a] transition-colors hover:bg-[#5c321a]/10 active:bg-[#5c321a]/15 disabled:opacity-35"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M3 8h10" /></svg>
                </button>
                <span className="min-w-9 text-center font-['Fraunces',serif] text-[21px] font-bold tabular-nums text-[#3d2010]" aria-live="polite">{quantity}</span>
                <button
                  type="button"
                  data-testid="product-card-increase"
                  aria-label="+"
                  disabled={quantity >= MAX_QUANTITY}
                  onClick={() => setQuantity((value) => Math.min(MAX_QUANTITY, value + 1))}
                  className="grid h-9 w-9 place-items-center rounded-full border-[1.5px] border-[#5c321a] text-[#5c321a] transition-colors hover:bg-[#5c321a]/10 active:bg-[#5c321a]/15 disabled:opacity-35"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M3 8h10M8 3v10" /></svg>
                </button>
              </div>

              <div className="mt-4">
                <textarea
                  id="product-card-note"
                  rows={1}
                  value={note}
                  maxLength={200}
                  aria-label={t.note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder={t.notePlaceholder}
                  className="block w-full resize-none border-b border-[#a88265]/50 bg-transparent pb-1 pt-1 font-['Montserrat',sans-serif] text-[13px] tracking-wide text-[#4a2c1d] outline-none placeholder:text-[#a88265]/70 focus:border-[#a85a2a] sm:text-[13.5px]"
                />
              </div>

              {refusal && (
                <p role="alert" className="mt-2.5 font-['Montserrat',sans-serif] text-[13px] font-semibold text-[#9a2b12]">{refusal}</p>
              )}
            </div>

            <div className="mt-6 flex gap-4 px-1">
              <button
                type="button"
                data-testid="product-card-add"
                disabled={!orderable}
                onClick={() => void submit('add')}
                style={{ transform: 'skewX(-14deg)' }}
                className="flex h-[46px] flex-1 items-center justify-center border-[1.8px] border-[#381f12] bg-transparent transition-all hover:bg-[#381f12]/5 active:scale-[0.98] disabled:opacity-40"
              >
                <span style={{ transform: 'skewX(14deg)' }} className="font-['Montserrat',sans-serif] text-[13.5px] font-bold uppercase tracking-[0.14em] text-[#381f12]">
                  {pending === 'add' ? '…' : t.add}
                </span>
              </button>
              <button
                type="button"
                data-testid="product-card-buy"
                disabled={!orderable}
                onClick={() => void submit('buy')}
                style={{ transform: 'skewX(-14deg)' }}
                className="flex h-[46px] flex-1 items-center justify-center bg-[#381f12] shadow-[0_6px_16px_-4px_rgba(45,18,6,0.4)] transition-all hover:bg-[#251208] active:scale-[0.98] disabled:opacity-40"
              >
                <span style={{ transform: 'skewX(14deg)' }} className="font-['Montserrat',sans-serif] text-[13.5px] font-bold uppercase tracking-[0.14em] text-[#fbf4ea]">
                  {pending === 'buy' ? '…' : t.buy}
                </span>
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export default ProductCard;
