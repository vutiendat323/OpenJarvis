import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { CustomerDock } from './CustomerDock';

describe('CustomerDock', () => {
  it('marks the selected tab and places its medallion without changing the plaque layout', () => {
    const markup = renderToStaticMarkup(<CustomerDock activeTab="help" cartCount={3} />);

    expect(markup).toContain('data-testid="customer-dock"');
    expect(markup).toContain('data-active-tab="help"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain('data-testid="customer-dock-medallion"');
    expect(markup).toContain('transform:translateX(200%)');
  });

  it('shows the live cart quantity and hides the badge for an empty cart', () => {
    const filled = renderToStaticMarkup(<CustomerDock activeTab="menu" cartCount={12} />);
    const empty = renderToStaticMarkup(<CustomerDock activeTab="menu" cartCount={0} />);

    expect(filled).toContain('data-testid="customer-dock-cart-badge"');
    expect(filled).toContain('>12</span>');
    expect(empty).not.toContain('customer-dock-cart-badge');
  });

  it('keeps each navigation control comfortably touch sized', () => {
    const markup = renderToStaticMarkup(<CustomerDock />);

    expect(markup.match(/data-testid="customer-dock-tab-/g)).toHaveLength(3);
    expect(markup).toContain('min-h-[72px]');
    expect(markup).toContain('active:scale-[0.96]');
  });

  it('renders localized Vietnamese tab labels when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(<CustomerDock uiLanguage="vi" />);

    expect(markup).toContain('GIỎ HÀNG');
    expect(markup).toContain('THỰC ĐƠN');
    expect(markup).toContain('TRỢ GIÚP');
    expect(markup).not.toContain('>CART<');
    expect(markup).not.toContain('>MENU<');
    expect(markup).not.toContain('>HELP<');
  });

  it('renders English tab labels when uiLanguage="en" or default', () => {
    const markup = renderToStaticMarkup(<CustomerDock />);

    expect(markup).toContain('CART');
    expect(markup).toContain('MENU');
    expect(markup).toContain('HELP');
    expect(markup).not.toContain('GIỎ HÀNG');
  });
});
