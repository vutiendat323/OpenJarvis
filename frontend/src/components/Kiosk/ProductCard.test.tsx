import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { CustomerMenuItem } from '@/pages/customerDisplayState';
import { ProductCard } from './ProductCard';

const rolls: CustomerMenuItem = {
  id: 'v-rolls',
  name: 'Cinnamon rolls',
  note: 'Bánh quế đường đen',
  price: 46000,
  available: true,
  category: 'món bánh',
  image_url: 'https://trendcoffee.net/api/latest/file/cinnamon.jpg',
  variants: [
    { id: 'v-rolls', size: 'tiêu chuẩn', price: 46000 },
    { id: 'v-rolls-l', size: 'lớn', price: 52000 },
  ],
};

function render(item: CustomerMenuItem, uiLanguage: 'en' | 'vi' = 'en'): string {
  return renderToStaticMarkup(
    <ProductCard item={item} uiLanguage={uiLanguage} onClose={() => {}} onSubmit={async () => {}} />,
  );
}

function tag(markup: string, testId: string): string {
  return markup.match(new RegExp(`<[a-z]+[^>]*data-testid="${testId}"[^>]*>`))?.[0] ?? '';
}

describe('ProductCard', () => {
  it('shows the live photo, description, price and every portion', () => {
    const markup = render(rolls);

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('src="https://trendcoffee.net/api/latest/file/cinnamon.jpg"');
    expect(markup).toContain('Cinnamon rolls');
    expect(markup).toContain('Bánh quế đường đen');
    expect(markup).toContain('46,000');
    expect(markup).toContain('LỚN - 52,000 VND');
    expect(markup).toContain('data-testid="portion-v-rolls-l"');
    expect(tag(markup, 'product-card-decrease')).toContain('disabled=""');
    expect(tag(markup, 'product-card-add')).not.toContain('disabled=""');
    expect(tag(markup, 'product-card-buy')).not.toContain('disabled=""');
  });

  it('keeps the paper card whole when the live menu has no photo', () => {
    const markup = render({ ...rolls, image_url: '' });

    expect(markup).toContain('data-testid="product-card-photo-fallback"');
    expect(markup).not.toContain('<img');
  });

  it('cannot be ordered once the live menu marks the item sold out', () => {
    const markup = render({ ...rolls, available: false });

    expect(markup).toContain('Sold out');
    expect(tag(markup, 'product-card-add')).toContain('disabled=""');
    expect(tag(markup, 'product-card-buy')).toContain('disabled=""');
  });

  it('cannot be ordered without a priced portion', () => {
    const markup = render({ name: 'Unpriced' });

    expect(tag(markup, 'product-card-add')).toContain('disabled=""');
  });

  it('labels its controls in Vietnamese for a Vietnamese display', () => {
    const markup = render(rolls, 'vi');

    expect(markup).toContain('Thêm vào giỏ');
    expect(markup).toContain('Mua ngay');
    expect(markup).toContain('Khẩu phần');
  });
});
