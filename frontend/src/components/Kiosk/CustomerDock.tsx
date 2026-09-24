import { useState, useRef } from 'react';

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

const R = 130; // Bán kính ảnh hưởng hiệu ứng Fisheye Wave (px)
const MAX_SCALE = 1.34; // Tỉ lệ phóng to cực đại khi hover trực diện (~1.34x)

const DOCK_ASSETS: Record<DockTab, string> = {
  cart: '/dock/cart.png',
  menu: '/dock/menu.png',
  help: '/dock/help.png',
};

function TabIcon({ tab, active }: { tab: DockTab; active: boolean }) {
  const assetSrc = DOCK_ASSETS[tab];
  return (
    <img
      src={assetSrc}
      alt=""
      aria-hidden="true"
      draggable={false}
      className={cn(
        'h-[62px] w-[62px] sm:h-[68px] sm:w-[68px] object-contain select-none transition-opacity duration-200',
        active ? 'opacity-100 drop-shadow-[0_2px_4px_rgba(90,27,0,0.25)]' : 'opacity-75 hover:opacity-100'
      )}
    />
  );
}

export function CustomerDock({
  activeTab: controlledTab,
  cartCount = 0,
  onTabChange,
  className = '',
  uiLanguage = 'en',
}: CustomerDockProps) {
  const [uncontrolledTab, setUncontrolledTab] = useState<DockTab>('menu');
  const [mouseX, setMouseX] = useState<number | null>(null);
  const dockRef = useRef<HTMLElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const activeTab = controlledTab ?? uncontrolledTab;
  const activeIndex = tabs.indexOf(activeTab);
  const count = Number.isFinite(cartCount) ? Math.max(0, Math.trunc(cartCount)) : 0;
  const isVi = uiLanguage === 'vi';

  const selectTab = (tab: DockTab) => {
    if (controlledTab === undefined) setUncontrolledTab(tab);
    onTabChange?.(tab);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLElement>) => {
    const rect = dockRef.current?.getBoundingClientRect();
    if (rect) {
      setMouseX(e.clientX - rect.left);
    }
  };

  const handleMouseLeave = () => {
    setMouseX(null);
  };

  const getScale = (index: number): number => {
    if (mouseX === null) return 1;
    const dockRect = dockRef.current?.getBoundingClientRect();
    if (!dockRect) return 1;

    const tabEl = tabRefs.current[index];
    const iconCenterX = tabEl
      ? tabEl.getBoundingClientRect().left + tabEl.getBoundingClientRect().width / 2 - dockRect.left
      : ((index + 0.5) / tabs.length) * dockRect.width;

    const distance = Math.abs(mouseX - iconCenterX);
    if (distance < R) {
      return 1 + (MAX_SCALE - 1) * Math.cos((distance / R) * (Math.PI / 2));
    }
    return 1;
  };

  return (
    <div
      className={cn(
        'pointer-events-none fixed bottom-[58px] sm:bottom-[62px] left-1/2 z-50 w-[min(520px,calc(100vw-1.5rem))] sm:w-[540px] -translate-x-1/2',
        className
      )}
    >
      <style>{`@keyframes customer-dock-badge-bump { 0% { transform: scale(.72); } 65% { transform: scale(1.14); } 100% { transform: scale(1); } }`}</style>
      <nav
        ref={dockRef}
        aria-label="Customer navigation"
        data-testid="customer-dock"
        data-active-tab={activeTab}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        className="pointer-events-auto relative flex h-[104px] sm:h-[108px] w-full select-none items-center justify-around"
      >
        {/* Vintage ornate outer frame */}
        <img
          src="/dock/vintage-border-only-transparent.png"
          alt=""
          aria-hidden="true"
          draggable={false}
          className="pointer-events-none absolute inset-0 z-10 h-full w-full select-none object-fill drop-shadow-[0_3px_8px_rgba(90,27,0,0.22)]"
        />

        {/* Vintage vertical separators between tabs - petite and elegant */}
        <img
          src="/dock/vintage-vertical-separator-transparent.png"
          alt=""
          aria-hidden="true"
          draggable={false}
          className="pointer-events-none absolute left-1/3 top-1/2 z-10 h-[42px] sm:h-[46px] -translate-x-1/2 -translate-y-1/2 select-none object-contain opacity-80"
        />
        <img
          src="/dock/vintage-vertical-separator-transparent.png"
          alt=""
          aria-hidden="true"
          draggable={false}
          className="pointer-events-none absolute left-2/3 top-1/2 z-10 h-[42px] sm:h-[46px] -translate-x-1/2 -translate-y-1/2 select-none object-contain opacity-80"
        />

        {/* macOS Active Dot Indicator (Sliding smoothly below active tab) */}
        <div
          data-testid="customer-dock-medallion"
          style={{ transform: `translateX(${activeIndex * 100}%)` }}
          className="pointer-events-none absolute -bottom-2.5 left-0 z-20 flex w-1/3 justify-center transform-gpu transition-transform duration-[260ms] ease-[cubic-bezier(0.25,1,0.5,1)]"
        >
          <span className="h-2 w-2 rounded-full bg-[#5a1b00] shadow-[0_1px_3px_rgba(90,27,0,0.45)]" />
        </div>

        {/* Interactive Tab Buttons with Fisheye Magnification */}
        <div className="relative z-30 grid h-full w-full grid-cols-3">
          {tabs.map((tab, idx) => {
            const active = tab === activeTab;
            const label = isVi ? tabLabelsVi[tab] : tabLabelsEn[tab];
            const scale = getScale(idx);

            return (
              <button
                key={tab}
                ref={(el) => { tabRefs.current[idx] = el; }}
                type="button"
                data-testid={`customer-dock-tab-${tab}`}
                aria-current={active ? 'page' : undefined}
                aria-label={label}
                onClick={() => selectTab(tab)}
                className="relative flex min-h-[72px] touch-manipulation flex-col items-center justify-center transition-transform duration-150 active:scale-[0.96] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#783820]"
              >
                {/* Scalable Icon Wrapper with Fisheye Magnification Wave */}
                <div
                  style={{ transform: `scale(${scale})` }}
                  className={cn(
                    'relative flex items-center justify-center origin-bottom transform-gpu',
                    mouseX === null ? 'transition-transform duration-200 ease-[cubic-bezier(0.25,1,0.5,1)]' : 'transition-transform duration-75 ease-out'
                  )}
                >
                  <TabIcon tab={tab} active={active} />
                  {tab === 'cart' && count > 0 && (
                    <span
                      key={count}
                      data-testid="customer-dock-cart-badge"
                      className="pointer-events-none absolute -right-1 -top-1 flex h-[19px] min-w-[19px] items-center justify-center rounded-full border border-[#fae7cd] bg-[#5c2912] px-1 font-['Fraunces',serif] text-[11px] font-bold leading-none text-[#fae7cd] shadow-md"
                      style={{ animation: 'customer-dock-badge-bump 260ms cubic-bezier(.25,1,.5,1)' }}
                    >
                      {count}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

export default CustomerDock;
