import { useState, type CSSProperties } from 'react';

import { cn } from '@/lib/utils';

export type DockTab = 'cart' | 'menu' | 'help';

export interface CustomerDockProps {
  activeTab?: DockTab;
  cartCount?: number;
  onTabChange?: (tab: DockTab) => void;
  className?: string;
  uiLanguage?: 'vi' | 'en';
}

const tabs: DockTab[] = ['cart', 'menu', 'help'];

const tabLabelsVi: Record<DockTab, string> = {
  cart: 'GIỎ HÀNG',
  menu: 'THỰC ĐƠN',
  help: 'TRỢ GIÚP',
};

const tabLabelsEn: Record<DockTab, string> = {
  cart: 'CART',
  menu: 'MENU',
  help: 'HELP',
};


function Fleuon({ active }: { active: boolean }) {
  const color = active ? '#fcecd7' : '#783820';
  return (
    <svg className="h-[7px] w-[46px] opacity-85" viewBox="0 0 46 7" fill="none" aria-hidden="true">
      <path d="M23 3.5C20 1 12 2 4 3.5c8 1.5 16 2.5 19 0Z" fill={color} />
      <path d="M23 3.5C26 1 34 2 42 3.5c-8 1.5-16 2.5-19 0Z" fill={color} />
      <circle cx="23" cy="3.5" r="1.8" fill={color} />
      <circle cx="1" cy="3.5" r="1" fill={color} />
      <circle cx="45" cy="3.5" r="1" fill={color} />
    </svg>
  );
}

function CartIcon({ active }: { active: boolean }) {
  return (
    <svg width="34" height="30" viewBox="0 0 34 30" fill="none" aria-hidden="true" className={active ? 'stroke-[#fcecd7]' : 'stroke-[#783820]'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 5h4l4 16h16l3-12H9" />
      <path d="M10.5 13h19M11.5 17h17M16 9l-.5 12M22 9v12" strokeWidth="1.2" />
      <circle cx="14" cy="25" r="2.8" />
      <circle cx="26" cy="25" r="2.8" />
      <circle cx="14" cy="25" r=".8" fill="currentColor" />
      <circle cx="26" cy="25" r=".8" fill="currentColor" />
    </svg>
  );
}

function BookIcon({ active }: { active: boolean }) {
  return (
    <svg width="34" height="28" viewBox="0 0 34 28" fill="none" aria-hidden="true" className={active ? 'stroke-[#fcecd7]' : 'stroke-[#783820]'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 6c-3-3-9-3-14-1v17c5-2 11-2 14 1 3-3 9-3 14-1V5c-5-2-11-2-14 1Z" />
      <path d="M17 6v17" strokeWidth="2" />
      <path d="M6 8.5c3.5-1 7-1 9 0.5M6 12c3.5-1 7-1 9 .5M6 15.5c3.5-1 7-1 9 .5M28 8.5c-3.5-1-7-1-9 .5M28 12c-3.5-1-7-1-9 .5M28 15.5c-3.5-1-7-1-9 .5" strokeWidth="1" />
    </svg>
  );
}

function HelpIcon({ active }: { active: boolean }) {
  return (
    <svg width="32" height="30" viewBox="0 0 32 30" fill="none" aria-hidden="true" className={active ? 'stroke-[#fcecd7]' : 'stroke-[#783820]'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="16" cy="15" r="12.5" strokeWidth="1.6" />
      <circle cx="16" cy="15" r="10.2" strokeWidth="1" strokeDasharray="2 2" />
      <path d="M13.5 11c0-2.5 1.5-3.5 3-3.5 1.7 0 3 1.3 3 3 0 1.7-1.5 3-3 4v2" strokeWidth="2" />
      <circle cx="16.5" cy="20.5" r="1.2" fill="currentColor" />
    </svg>
  );
}

function TabIcon({ tab, active }: { tab: DockTab; active: boolean }) {
  if (tab === 'cart') return <CartIcon active={active} />;
  if (tab === 'menu') return <BookIcon active={active} />;
  return <HelpIcon active={active} />;
}

export function CustomerDock({
  activeTab: controlledTab,
  cartCount = 0,
  onTabChange,
  className = '',
  uiLanguage = 'en',
}: CustomerDockProps) {
  const [uncontrolledTab, setUncontrolledTab] = useState<DockTab>('menu');
  const activeTab = controlledTab ?? uncontrolledTab;
  const activeIndex = tabs.indexOf(activeTab);
  const count = Number.isFinite(cartCount) ? Math.max(0, Math.trunc(cartCount)) : 0;
  const medallionStyle = { transform: `translateX(${activeIndex * 100}%)` } satisfies CSSProperties;
  const isVi = uiLanguage === 'vi';

  const selectTab = (tab: DockTab) => {
    if (controlledTab === undefined) setUncontrolledTab(tab);
    onTabChange?.(tab);
  };

  return (
    <div className={cn('pointer-events-none fixed bottom-[66px] left-1/2 z-50 w-[min(440px,calc(100vw-2rem))] -translate-x-1/2', className)}>
      <style>{`@keyframes customer-dock-badge-bump { 0% { transform: scale(.72); } 65% { transform: scale(1.14); } 100% { transform: scale(1); } }`}</style>
      <nav
        aria-label="Customer navigation"
        data-testid="customer-dock"
        data-active-tab={activeTab}
        className="pointer-events-auto relative h-[96px] w-full select-none drop-shadow-[0_10px_20px_rgba(90,42,18,0.18)]"
      >
        <svg className="pointer-events-none absolute inset-0 h-full w-full overflow-visible" viewBox="0 0 440 96" fill="none" aria-hidden="true">
          <defs>
            <linearGradient id="customer-dock-parchment" x1="0" y1="0" x2="0" y2="1">
              <stop stopColor="#fae7cd" />
              <stop offset="1" stopColor="#f5dcbc" />
            </linearGradient>
          </defs>
          <path d="M34 8h372c6 0 8 5 8 10 0 6 5 8 10 8 5 0 8 3 8 10v24c0 7-3 10-8 10-5 0-10 2-10 8 0 5-2 10-8 10H34c-6 0-8-5-8-10 0-6-5-8-10-8-5 0-8-3-8-10V36c0-7 3-10 8-10 5 0 10-2 10-8 0-5 2-10 8-10Z" fill="url(#customer-dock-parchment)" stroke="#68341a" strokeWidth="2.2" />
          <path d="M36 12h368c5 0 6 4 6 8 0 6 6 8 10 8 4 0 7 3 7 10v20c0 7-3 10-7 10-4 0-10 2-10 8 0 4-1 8-6 8H36c-5 0-6-4-6-8 0-6-6-8-10-8-4 0-7-3-7-10V38c0-7 3-10 7-10 4 0 10-2 10-8 0-4 1-8 6-8Z" stroke="#68341a" strokeWidth="1.2" strokeOpacity=".8" />
        </svg>

        <div
          data-testid="customer-dock-medallion"
          style={medallionStyle}
          className="pointer-events-none absolute -top-2.5 -bottom-2.5 left-0 z-10 w-1/3 transform-gpu transition-transform duration-[260ms] ease-[cubic-bezier(0.25,1,0.5,1)]"
        >
          <svg className="absolute left-1/2 h-[116px] w-[128px] -translate-x-1/2" viewBox="0 0 140 120" fill="none" aria-hidden="true">
            <path d="M14 26c0-10 9-12 24-16C53 6 60 3 70 3s17 3 32 7c15 4 24 6 24 16v68c0 10-9 12-24 16-15 4-22 7-32 7s-17-3-32-7c-15-4-24-6-24-16V26Z" fill="#68341a" stroke="#4e2410" strokeWidth="2" />
            <path d="M19 28c0-8 8-10 21-14 13-4 20-7 30-7s17 3 30 7c13 4 21 6 21 14v64c0 8-8 10-21 14-13 4-20 7-30 7s-17-3-30-7c-13-4-21-6-21-14V28Z" stroke="#fae7cd" strokeWidth="1.2" strokeOpacity=".85" />
          </svg>
        </div>

        <div className="pointer-events-none absolute top-3.5 bottom-3.5 left-1/3 z-20 flex w-px items-center justify-center">
          <div className="h-full w-px bg-gradient-to-b from-transparent via-[#783820] to-transparent opacity-45" />
          <div className="absolute h-1.5 w-1.5 rotate-45 bg-[#783820] opacity-85" />
        </div>
        <div className="pointer-events-none absolute top-3.5 bottom-3.5 right-1/3 z-20 flex w-px items-center justify-center">
          <div className="h-full w-px bg-gradient-to-b from-transparent via-[#783820] to-transparent opacity-45" />
          <div className="absolute h-1.5 w-1.5 rotate-45 bg-[#783820] opacity-85" />
        </div>

        <div className="relative z-30 grid h-full grid-cols-3">
          {tabs.map((tab) => {
            const active = tab === activeTab;
            const label = isVi ? tabLabelsVi[tab] : tabLabelsEn[tab];
            return (
              <button
                key={tab}
                type="button"
                data-testid={`customer-dock-tab-${tab}`}
                aria-current={active ? 'page' : undefined}
                aria-label={label}
                onClick={() => selectTab(tab)}
                className="relative flex min-h-[72px] touch-manipulation flex-col items-center justify-center transition-transform duration-150 active:scale-[0.96] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-6px] focus-visible:outline-[#68341a]"
              >
                <div className="relative mb-0.5 flex h-[38px] w-[44px] items-center justify-center">
                  <TabIcon tab={tab} active={active} />
                  {tab === 'cart' && count > 0 && (
                    <span
                      key={count}
                      data-testid="customer-dock-cart-badge"
                      className={`absolute -right-1 -top-1 flex min-h-[18px] min-w-[18px] items-center justify-center rounded-full border px-1 font-['Playfair_Display',serif] text-[11px] font-bold leading-none shadow ${active ? 'border-[#5c2912] bg-[#fae7cd] text-[#5c2912]' : 'border-[#fae7cd] bg-[#5c2912] text-[#fae7cd]'}`}
                      style={{ animation: 'customer-dock-badge-bump 260ms cubic-bezier(.25,1,.5,1)' }}
                    >
                      {count}
                    </span>
                  )}
                </div>
                <span className={`font-['Playfair_Display',serif] text-[13px] font-bold ${isVi ? 'tracking-[0.14em]' : 'tracking-[0.24em]'} ${active ? 'text-[#fcecd7]' : 'text-[#783820]'}`}>{label}</span>
                <Fleuon active={active} />
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

export default CustomerDock;
