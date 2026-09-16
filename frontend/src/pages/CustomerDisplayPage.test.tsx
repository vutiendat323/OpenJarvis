import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import {
  BillView,
  CartView,
  CustomerDisplayPage,
  MenuView,
  PaymentQrView,
} from './CustomerDisplayPage';

function renderMenu({
  items,
  menuItems = items,
  displayMode = 'filtered',
  preview = false,
}: {
  items: { id: string; name: string; price: number; category?: string; is_top_sell?: boolean; is_new?: boolean }[];
  menuItems?: { id: string; name: string; price: number; category?: string; is_top_sell?: boolean; is_new?: boolean }[];
  displayMode?: 'browse' | 'filtered';
  preview?: boolean;
}): string {
  return renderToStaticMarkup(
    <MenuView
      items={items}
      menuItems={menuItems}
      displayMode={displayMode}
      resultComplete={!preview}
      projectedCount={items.length}
      publishedCount={items.length}
      preview={preview}
    />,
  );
}

describe('MenuView', () => {
  it('renders every filtered result without a recommendation limit', () => {
    const items = Array.from({ length: 100 }, (_, index) => ({
      id: `item-${index}`,
      name: `Item ${index}`,
      price: index,
      category: 'món ăn',
    }));
    const markup = renderMenu({ items });

    expect(markup.match(/data-menu-item=/g)).toHaveLength(100);
  });

  it('renders only live top-sell and new items as browse recommendations', () => {
    const menuItems = [
      { id: 'top', name: 'Top Seller', price: 60000, category: 'cà phê', is_top_sell: true },
      { id: 'new', name: 'New Drink', price: 55000, category: 'món trà', is_new: true },
      { id: 'regular', name: 'Regular Dish', price: 65000, category: 'món ăn' },
    ];
    const markup = renderMenu({ items: menuItems, menuItems, displayMode: 'browse' });

    expect(markup.match(/data-menu-item=/g)).toHaveLength(2);
    expect(markup).toContain('Top Seller');
    expect(markup).toContain('New Drink');
    expect(markup).toContain('Regular Dish');
    expect(markup.indexOf('Regular Dish')).toBeLessThan(markup.indexOf('RECOMMENDATIONS'));
  });

  it('groups the complete live catalog by category in a dedicated scroll area', () => {
    const markup = renderMenu({
      items: [{ id: 'coffee', name: 'Coffee Trend', price: 60000, category: 'cà phê' }],
      menuItems: [
        { id: 'coffee', name: 'Coffee Trend', price: 60000, category: 'cà phê' },
        { id: 'tea', name: 'Matcha Trend', price: 60000, category: 'món trà' },
      ],
    });

    expect(markup).toContain('CÀ PHÊ');
    expect(markup).toContain('MÓN TRÀ');
    expect(markup.match(/data-catalog-item=/g)).toHaveLength(2);
    expect(markup).toContain('data-menu-scroll="true"');
    expect(markup).toContain('overflow-y-auto');
  });

  it('keeps recommendations compact while widening the menu to 70/30', () => {
    const menuItems = [
      { id: 'coffee', name: 'Coffee Trend', price: 60000, category: 'cà phê', is_top_sell: true },
      { id: 'tea', name: 'Matcha Trend', price: 60000, category: 'món trà', is_top_sell: true },
      { id: 'smoothie', name: 'Đậu đỏ đá xay', price: 55000, category: 'sinh tố', is_new: true },
      { id: 'food', name: 'Taco gà', price: 86000, category: 'món ăn', is_top_sell: true },
      { id: 'cake', name: 'Bánh tiramisu', price: 39000, category: 'món bánh', is_new: true },
      { id: 'wine', name: 'Corona Extra', price: 89000, category: 'bia/ rượu vang' },
    ];
    const markup = renderMenu({ items: menuItems, menuItems, displayMode: 'browse' });

    expect(markup).toContain('data-menu-layout="asymmetric"');
    expect(markup).toContain('md:grid-cols-[minmax(0,7fr)_minmax(18rem,3fr)]');
    expect(markup).toContain('lg:grid-cols-3');
    expect(markup).toContain('data-recommendations-layout="compact"');
    expect(markup).toMatch(
      /data-catalog-item="true" class="[^"]*text-\[15px\][^"]*sm:text-\[16px\]/,
    );
  });

  it('keeps categories unbroken in the aligned three-column grid', () => {
    const foodItems = Array.from({ length: 40 }, (_, index) => ({
      id: `food-${index}`,
      name: `Món ăn ${index + 1}`,
      price: 60000,
      category: 'món ăn',
    }));
    const markup = renderMenu({ items: foodItems, menuItems: foodItems });

    expect(markup.match(/data-catalog-item=/g)).toHaveLength(40);
    expect(markup).not.toContain('MÓN ĂN (TIẾP)');
  });

  it('places similarly sized categories together and leaves the longest category for the final row', () => {
    const menuItems = [
      ...Array.from({ length: 15 }, (_, index) => ({
        id: `food-${index}`,
        name: `Món ăn ${index + 1}`,
        price: 60000,
        category: 'món ăn',
      })),
      ...Array.from({ length: 2 }, (_, index) => ({
        id: `water-${index}`,
        name: `Nước ${index + 1}`,
        price: 30000,
        category: 'nước giải khát',
      })),
      ...Array.from({ length: 3 }, (_, index) => ({
        id: `cake-${index}`,
        name: `Bánh ${index + 1}`,
        price: 40000,
        category: 'món bánh',
      })),
      ...Array.from({ length: 4 }, (_, index) => ({
        id: `coffee-${index}`,
        name: `Cà phê ${index + 1}`,
        price: 50000,
        category: 'cà phê',
      })),
    ];
    const markup = renderMenu({ items: menuItems, menuItems });
    const rows = markup.split('data-menu-row="true"').slice(1);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain('NƯỚC GIẢI KHÁT');
    expect(rows[0]).toContain('MÓN BÁNH');
    expect(rows[0]).toContain('CÀ PHÊ');
    expect(rows[1]).toContain('MÓN ĂN');
  });

  it('gives the customer display route its own viewport scroll container', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=menu']}>
        <CustomerDisplayPage />
      </MemoryRouter>,
    );
    const rootClass = markup.match(/<div class="([^"]+)"/)?.[1].split(' ') ?? [];

    expect(rootClass).toContain('h-screen');
    expect(rootClass).toContain('overflow-y-auto');
    expect(rootClass).not.toContain('min-h-screen');
  });

  it('renders verified zero as no results, never demo content', () => {
    const markup = renderMenu({
      items: [],
      menuItems: [
        { id: 'coffee', name: 'Coffee Trend', price: 60000, category: 'cà phê' },
      ],
    });

    expect(markup).not.toContain('data-menu-item=');
    expect(markup).toContain('No matching items found.');
    expect(markup).toContain('data-catalog-item=');
    expect(markup).toContain('Coffee Trend');
    expect(markup).not.toContain('MINCED BEEF SPAGHETTI');
    expect(markup).not.toContain('CATEGORIES');
  });

  it('uses English UI copy for empty and waiting customer-display states', () => {
    const menuMarkup = renderMenu({
      items: [],
      menuItems: [{ id: 'coffee', name: 'Coffee Trend', price: 60000, category: 'cà phê' }],
    });
    const billMarkup = renderToStaticMarkup(
      <BillView
        order_id="TC-1"
        branch="Trend Coffee"
        status="pending"
        lines={[]}
        total={0}
      />,
    );
    const disconnectedMarkup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display']}>
        <CustomerDisplayPage />
      </MemoryRouter>,
    );
    const waitingMarkup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=waiting']}>
        <CustomerDisplayPage />
      </MemoryRouter>,
    );

    expect(menuMarkup).toContain('No matching items found.');
    expect(menuMarkup).not.toContain('Không có kết quả phù hợp');
    expect(menuMarkup).not.toContain('Cormorant_Garamond');
    expect(billMarkup).toContain('Provider returned no line items.');
    expect(billMarkup).not.toContain('Provider không trả về dòng món nào.');
    expect(disconnectedMarkup).toContain('Ready to serve');
    expect(disconnectedMarkup).not.toContain('Sẵn sàng phục vụ');
    expect(waitingMarkup).toContain('Start your day with fresh coffee and a crisp French croissant.');
    expect(waitingMarkup).not.toContain('Khởi đầu ngày mới');
  });

  it('keeps intentional menu preview demo content', () => {
    const markup = renderMenu({ items: [], preview: true });

    expect(markup).toContain('MINCED BEEF SPAGHETTI');
    expect(markup).toContain('data-menu-item=');
  });
});

describe('CartView', () => {
  it('renders only the local cart facts and never invents an order', () => {
    const markup = renderToStaticMarkup(
      <CartView
        lines={[{
          line_id: 'line-1',
          name: 'Cà phê sữa',
          size: 'tiêu chuẩn',
          note: 'ít đá',
          quantity: 3,
          unit_price: 40000,
          line_total: 120000,
        }]}
        total={120000}
        order_note="Làm nhanh giúp mình"
        order_type="at-table"
        table_name="73"
      />,
    );

    expect(markup).toContain('CART');
    expect(markup).toContain('ORDER NOT CREATED');
    expect(markup).toContain('Cà phê sữa');
    expect(markup).toContain('ít đá');
    expect(markup).toContain('Làm nhanh giúp mình');
    expect(markup).toContain('ORDER TYPE: AT TABLE');
    expect(markup).toContain('73');
    expect(markup).not.toContain('ORDER SLIP');
    expect(markup).not.toContain('TC-000245');
    expect(markup).not.toContain('AUGUST 28, 2026');
    expect(markup).toContain('CREDIT &amp; DEBIT CARDS: VISA, MASTERCARD, NAPAS');
    expect(markup).toContain('ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE');
    expect(markup).toContain('MB BANK: 9999.8888.68');
    expect(markup).toContain('SERVICE CHARGE (10%)');
    expect(markup).toContain('TOTAL ESTIMATED');
    expect(markup).toContain('STATUS: ORDER IN PROGRESS (PLEASE CHECK YOUR ITEMS).');
    expect(markup).toContain('TREND COFFEE &amp; RESTAURANT');
    expect(markup).toContain('THANK YOU FOR DINING WITH US.');
    expect(markup).not.toContain('HÌNH THỨC:');
    expect(markup).not.toContain('TỔNG TẠM TÍNH');
    expect(markup.indexOf('SUBTOTAL')).toBeLessThan(
      markup.indexOf('CREDIT &amp; DEBIT CARDS'),
    );
    expect(markup).toContain('mt-8 max-w-[440px]');
    expect(markup).toContain('mt-6 flex flex-wrap');
    expect(markup).not.toContain('mt-auto flex flex-wrap');
  });
});

describe('BillView', () => {
  it('renders invoice luxury layout and items', () => {
    const markup = renderToStaticMarkup(
      <BillView
        order_id="order-1"
        branch="Trend Coffee Thủ Đức"
        order_type="take-out"
        status="pending"
        lines={[{
          line_id: 'line-1',
          name: 'Cà phê đen',
          quantity: 1,
          unit_price: 35000,
          line_total: 35000,
        }]}
        total={35000}
      />,
    );

    expect(markup).toContain('INVOICE');
    expect(markup).toContain('order-1');
    expect(markup).toContain('PENDING');
    expect(markup).toContain('Trend Coffee Thủ Đức');
    expect(markup).toContain('ORDER TYPE: TAKE OUT');
    expect(markup).toContain('RESERVATIONS@TRENDCOFFEE.VN | +84 90 123 4567');
    expect(markup).toContain('WWW.TRENDCOFFEE.NET');
    expect(markup).toContain('SUBTOTAL');
    expect(markup).toContain('SERVICE CHARGE (10%)');
    expect(markup).toContain('TOTAL AMOUNT DUE');
    expect(markup).toContain('CREDIT &amp; DEBIT CARDS: VISA, MASTERCARD, AMERICAN EXPRESS, NAPAS');
    expect(markup).toContain('ACCOUNT NAME: CONG TY CO PHAN TREND COFFEE');
    expect(markup).toContain('MB BANK: 9999.8888.68');
    expect(markup).toContain('THANK YOU FOR DINING WITH US.');
    expect(markup).not.toContain('HÌNH THỨC:');
    expect(markup).not.toContain('INV-000245');
    expect(markup).not.toContain('30-71234567-8');
  });
});

describe('PaymentQrView', () => {
  it('renders a complete receipt with a compact safe QR image', () => {
    const imageMarkup = renderToStaticMarkup(
      <PaymentQrView
        qr_code="data:image/png;base64,abc"
        total={70000}
        order_id="order-1"
        status="pending"
        order_type="at-table"
        branch="ba9355f797"
        table_name="73"
        lines={[{
          name: 'Cà phê đen',
          size: 'tiêu chuẩn',
          quantity: 2,
          unit_price: 35000,
          line_total: 70000,
        }]}
      />,
    );
    const unsafeMarkup = renderToStaticMarkup(
      <PaymentQrView
        qr_code="merchant-opaque-qr"
        total={35000}
        order_id="order-1"
      />,
    );

    expect(imageMarkup).toContain('src="data:image/png;base64,abc"');
    expect(unsafeMarkup).not.toContain('<img');
    expect(unsafeMarkup).toContain('UNABLE TO DISPLAY A VERIFIED QR CODE.');
    expect(imageMarkup).toContain('INVOICE');
    expect(imageMarkup).toContain('INVOICE NO.: order-1');
    expect(imageMarkup).toContain('STATUS: PENDING PAYMENT');
    expect(imageMarkup).toContain('ORDER TYPE: AT TABLE');
    expect(imageMarkup).toContain('TABLE: 73');
    expect(imageMarkup).toContain('Cà phê đen');
    expect(imageMarkup).toContain('SERVICE CHARGE (10%)');
    expect(imageMarkup).toContain('TOTAL AMOUNT DUE');
    expect(imageMarkup).toContain('MB BANK: 9999.8888.68');
    expect(imageMarkup).toContain('THANK YOU FOR DINING WITH US.');
    expect(imageMarkup).toContain('h-36 w-36');
    expect(imageMarkup).toContain('mix-blend-multiply');
    expect(imageMarkup).not.toContain('bg-white');
    expect(imageMarkup).not.toContain('border border-[#c4ab91]');
    expect(imageMarkup).not.toContain('h-32 w-32');
    expect(imageMarkup).not.toContain('h-64 w-64');
  });
});
