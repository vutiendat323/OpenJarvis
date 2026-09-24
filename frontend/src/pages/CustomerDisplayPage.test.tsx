import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import {
  BillView,
  CartView,
  type CartTouchControls,
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

describe('MenuView touch search', () => {
  const catalog = [
    { id: 'top', name: 'Coffee Trend', price: 60000, category: 'cà phê', is_top_sell: true, note: '' },
    { id: 'tea', name: 'Trà đào cam sả', price: 55000, category: 'món trà', note: 'Trà đào tươi' },
    { id: 'milk-tea', name: 'Trà sữa', price: 45000, category: 'món trà' },
    { id: 'cake', name: 'Bánh Tiramisu', price: 39000, category: 'món bánh' },
  ];

  function renderSearch(searchQuery: string, uiLanguage: 'en' | 'vi' = 'en'): string {
    return renderToStaticMarkup(
      <MenuView
        items={catalog}
        menuItems={catalog}
        displayMode="browse"
        resultComplete
        projectedCount={catalog.length}
        publishedCount={catalog.length}
        preview={false}
        uiLanguage={uiLanguage}
        onSelectItem={() => {}}
        searchQuery={searchQuery}
        onSearchChange={() => {}}
      />,
    );
  }

  const recommendations = (markup: string) => [...markup.matchAll(/data-menu-item[^>]*>.*?<span class="truncate pr-2">([^<]*)<\/span>/g)].map((match) => match[1]);

  it('offers an empty search field over the featured recommendations', () => {
    const markup = renderSearch('');

    expect(markup).toContain('placeholder="Search menu..."');
    expect(markup).toContain('autoComplete="off"');
    expect(markup).not.toContain('data-testid="menu-search-clear"');
    expect(recommendations(markup)).toEqual(['Coffee Trend']);
  });

  it('shows every live match with its price and note in place of the recommendations', () => {
    const markup = renderSearch('tra');

    expect(recommendations(markup)).toEqual(['Trà đào cam sả', 'Trà sữa']);
    expect(markup).toContain('Trà đào tươi');
    expect(markup).toMatch(/Trà sữa<\/span><span[^>]*>45<\/span>/);
    expect(markup).toContain('data-testid="menu-search-clear"');
    expect(markup.match(/data-catalog-item/g)).toHaveLength(catalog.length);
  });

  it('says so when nothing matches the typed words', () => {
    const markup = renderSearch('matcha', 'vi');

    expect(markup).toContain('placeholder="Tìm kiếm sản phẩm..."');
    expect(markup).toContain('Không có kết quả phù hợp.');
    expect(recommendations(markup)).toEqual([]);
  });

  it('treats a blank query as no search', () => {
    expect(recommendations(renderSearch('   '))).toEqual(['Coffee Trend']);
  });

  it('keeps the read-only display without a search field', () => {
    const markup = renderMenu({ items: catalog, menuItems: catalog, displayMode: 'browse' });

    expect(markup).not.toContain('<input');
  });
});

describe('MenuView', () => {
  it('makes every catalog row and recommendation a touch target', () => {
    const menuItems = [
      { id: 'top', name: 'Top Seller', price: 60000, category: 'cà phê', is_top_sell: true },
      { id: 'regular', name: 'Regular Dish', price: 65000, category: 'món ăn' },
    ];
    const markup = renderToStaticMarkup(
      <MenuView
        items={menuItems}
        menuItems={menuItems}
        displayMode="browse"
        resultComplete
        projectedCount={2}
        publishedCount={2}
        preview={false}
        onSelectItem={() => {}}
      />,
    );

    expect(markup.match(/<button type="button"[^>]*data-catalog-item/g)).toHaveLength(2);
    expect(markup.match(/<button type="button"[^>]*data-menu-item/g)).toHaveLength(1);
  });

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

  it('renders a touch-available dock with MENU selected for the menu display', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=menu']}>
        <CustomerDisplayPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('data-testid="customer-dock"');
    expect(markup).toContain('data-active-tab="menu"');
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

  it('renders localized Vietnamese ribbons and empty message when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(
      <MenuView
        items={[]}
        menuItems={[{ id: 'coffee', name: 'Coffee Trend', price: 60000, category: 'cà phê' }]}
        displayMode="filtered"
        resultComplete={true}
        projectedCount={0}
        publishedCount={0}
        preview={false}
        uiLanguage="vi"
      />,
    );

    expect(markup).toContain('THỰC ĐƠN');
    expect(markup).toContain('GỢI Ý HÔM NAY');
    expect(markup).toContain('Không có kết quả phù hợp.');
  });
});

const noop = () => {};

function touchControls(overrides: Partial<CartTouchControls> = {}): CartTouchControls {
  return {
    busy: false,
    error: null,
    tables: [
      { slug: 't1', name: '1', status: 'available' },
      { slug: 't2', name: '2', status: 'reserved' },
    ],
    onQuantity: noop,
    onRemove: noop,
    onClear: noop,
    onOrderType: noop,
    onTable: noop,
    onRefreshTables: noop,
    onPickup: noop,
    onCheckout: noop,
    ...overrides,
  };
}

const cola = {
  line_id: 'line-1',
  name: 'Coca Cola',
  size: 'tiêu chuẩn',
  quantity: 1,
  unit_price: 35000,
  line_total: 35000,
};

function renderTouchCart(props: Partial<Parameters<typeof CartView>[0]> = {}): string {
  return renderToStaticMarkup(
    <CartView
      lines={[cola]}
      total={35000}
      order_note=""
      order_type="at-table"
      table="t2"
      table_name="2"
      pickup_minutes={0}
      controls={touchControls()}
      {...props}
    />,
  );
}

function tagFor(markup: string, testId: string): string {
  return markup.match(new RegExp(`<[a-z]+[^>]*data-testid="${testId}"[^>]*>`))?.[0] ?? '';
}

describe('CartView touch controls', () => {
  it('edits each line and checks out a dine-in draft for its total', () => {
    const markup = renderTouchCart();

    expect(tagFor(markup, 'cart-line-decrease')).toContain('disabled=""');
    expect(tagFor(markup, 'cart-line-increase')).not.toContain('disabled=""');
    expect(markup).toContain('data-testid="cart-line-remove"');
    expect(markup).toContain('data-testid="cart-clear"');
    expect(markup).toContain('data-testid="cart-order-type"');
    expect(markup).toMatch(/<option value="t2" selected="">2 · RESERVED<\/option>/);
    expect(markup).toContain('<option value="t1">1 · AVAILABLE</option>');
    expect(markup).not.toContain('data-testid="cart-pickup-time"');
    expect(tagFor(markup, 'cart-checkout')).not.toContain('disabled=""');
    expect(markup).toContain('CHECKOUT');
  });

  it('asks for the missing order choice before checkout', () => {
    const noTable = renderTouchCart({ table: '', table_name: '' });
    const noType = renderTouchCart({ order_type: '', table: '', table_name: '' });
    const empty = renderTouchCart({ lines: [], total: 0 });

    expect(tagFor(noTable, 'cart-checkout')).toContain('disabled=""');
    expect(noTable).toContain('SELECT A TABLE TO CHECK OUT');
    expect(noTable).toContain('1 · AVAILABLE');
    expect(noTable).toContain('max-h-60 overflow-y-auto');
    expect(tagFor(noType, 'cart-checkout')).toContain('disabled=""');
    expect(noType).not.toContain('CHOOSE DINE-IN OR TAKE-OUT TO CHECK OUT');
    expect(noType).not.toContain('data-testid="cart-table"');
    expect(tagFor(empty, 'cart-checkout')).toContain('disabled=""');
  });

  it('offers the merchant pickup times instead of tables for take-out', () => {
    const markup = renderTouchCart({ order_type: 'take-out', table: '', table_name: '', pickup_minutes: 15 });

    expect(markup).not.toContain('data-testid="cart-table"');
    expect(markup.match(/<option value="(0|5|10|15|30|45|60)"/g)).toHaveLength(7);
    expect(markup).toContain('<option value="0">IMMEDIATELY</option>');
    expect(markup).toContain('<option value="15" selected="">15 MINUTES</option>');
    expect(tagFor(markup, 'cart-checkout')).not.toContain('disabled=""');
  });

  it('locks every control while a tap is saved and shows the refusal', () => {
    const markup = renderTouchCart({
      lines: [{ ...cola, quantity: 2, line_total: 70000 }],
      controls: touchControls({ busy: true, error: 'voice_session_required' }),
    });

    for (const id of ['cart-line-decrease', 'cart-line-increase', 'cart-line-remove', 'cart-clear', 'cart-order-type', 'cart-table', 'cart-checkout']) {
      expect(tagFor(markup, id)).toContain('disabled=""');
    }
    expect(markup).toContain('role="alert"');
    expect(markup).toContain('Start a chat with the assistant to order.');
  });

  it('labels the touch receipt in Vietnamese', () => {
    const markup = renderTouchCart({ uiLanguage: 'vi', order_type: 'take-out', table: '', table_name: '' });

    expect(markup).toContain('THANH TOÁN');
    expect(markup).toContain('MANG VỀ');
    expect(markup).toContain('NGAY LẬP TỨC');
    expect(markup).toContain('XÓA TẤT CẢ');
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
    expect(markup).not.toContain('<button');
    expect(markup).not.toContain('<select');
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

  it('renders localized Vietnamese receipt text when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(
      <CartView
        uiLanguage="vi"
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

    expect(markup).toContain('GIỎ HÀNG');
    expect(markup).toContain('CHƯA TẠO ĐƠN');
    expect(markup).toContain('Cà phê sữa');
    expect(markup).toContain('Ghi chú: ít đá');
    expect(markup).toContain('GHI CHÚ ĐƠN HÀNG: ');
    expect(markup).toContain('Làm nhanh giúp mình');
    expect(markup).toContain('LOẠI ĐƠN: TẠI BÀN');
    expect(markup).toContain('BÀN: 73');
    expect(markup).toContain('MÓN');
    expect(markup).toContain('SL');
    expect(markup).toContain('ĐƠN GIÁ');
    expect(markup).toContain('TẠM TÍNH');
    expect(markup).toContain('PHÍ DỊCH VỤ (10%)');
    expect(markup).toContain('TỔNG CỘNG (TẠM TÍNH)');
    expect(markup).toContain('THẺ TÍN DỤNG &amp; GHI NỢ: VISA, MASTERCARD, NAPAS');
    expect(markup).toContain('CHUYỂN KHOẢN:');
    expect(markup).toContain('TÊN TÀI KHOẢN: CONG TY CO PHAN TREND COFFEE');
    expect(markup).toContain('TRẠNG THÁI: ĐANG XỬ LÝ ĐƠN HÀNG (VUI LÒNG KIỂM TRA MÓN).');
    expect(markup).toContain('CẢM ƠN QUÝ KHÁCH ĐÃ GHÉ THĂM.');
  });

  it('renders localized empty cart message when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(
      <CartView
        uiLanguage="vi"
        lines={[]}
        total={0}
        order_note=""
        order_type=""
        table_name=""
      />,
    );

    expect(markup).toContain('GIỎ HÀNG ĐANG TRỐNG.');
    expect(markup).not.toContain('CART IS EMPTY.');
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

  it('renders localized Vietnamese bill text when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(
      <BillView
        uiLanguage="vi"
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

    expect(markup).toContain('HÓA ĐƠN');
    expect(markup).toContain('MÃ HÓA ĐƠN: order-1');
    expect(markup).toContain('TRẠNG THÁI: CHỜ XỬ LÝ');
    expect(markup).toContain('CHI NHÁNH: Trend Coffee Thủ Đức');
    expect(markup).toContain('LOẠI ĐƠN: MANG VỀ');
    expect(markup).toContain('MÓN');
    expect(markup).toContain('SL');
    expect(markup).toContain('ĐƠN GIÁ');
    expect(markup).toContain('TẠM TÍNH');
    expect(markup).toContain('PHÍ DỊCH VỤ (10%)');
    expect(markup).toContain('TỔNG TIỀN THANH TOÁN');
    expect(markup).toContain('THANH TOÁN TIỀN MẶT: TẠI QUẦY.');
    expect(markup).toContain('CẢM ƠN QUÝ KHÁCH ĐÃ GHÉ THĂM.');
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

  it('renders localized Vietnamese payment QR text when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(
      <PaymentQrView
        uiLanguage="vi"
        qr_code="data:image/png;base64,abc"
        total={70000}
        order_id="order-1"
        status="pending"
        order_type="at-table"
        branch="ba9355f797"
        table_name="73"
        lines={[{
          name: 'Cà phê đen',
          quantity: 2,
          unit_price: 35000,
          line_total: 70000,
        }]}
      />,
    );

    expect(markup).toContain('HÓA ĐƠN');
    expect(markup).toContain('MÃ HÓA ĐƠN: order-1');
    expect(markup).toContain('TRẠNG THÁI: CHỜ THANH TOÁN');
    expect(markup).toContain('CHI NHÁNH: ba9355f797');
    expect(markup).toContain('LOẠI ĐƠN: TẠI BÀN');
    expect(markup).toContain('BÀN: 73');
    expect(markup).toContain('MÓN');
    expect(markup).toContain('SL');
    expect(markup).toContain('ĐƠN GIÁ');
    expect(markup).toContain('TẠM TÍNH');
    expect(markup).toContain('PHÍ DỊCH VỤ (10%)');
    expect(markup).toContain('TỔNG TIỀN THANH TOÁN');
    expect(markup).toContain('CẢM ƠN QUÝ KHÁCH ĐÃ GHÉ THĂM.');
  });
});

describe('CustomerDisplayPage language sync', () => {
  it('renders localized dock and menu header ribbons when uiLanguage="vi"', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=menu']}>
        <CustomerDisplayPage uiLanguage="vi" />
      </MemoryRouter>,
    );

    expect(markup).toContain('GIỎ HÀNG');
    expect(markup).toContain('THỰC ĐƠN');
    expect(markup).toContain('TRỢ GIÚP');
    expect(markup).toContain('GỢI Ý HÔM NAY');
  });

  it('renders English dock and menu ribbons by default or when uiLanguage="en"', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=menu']}>
        <CustomerDisplayPage uiLanguage="en" />
      </MemoryRouter>,
    );

    expect(markup).toContain('CART');
    expect(markup).toContain('MENU');
    expect(markup).toContain('HELP');
    expect(markup).toContain('RECOMMENDATIONS');
  });

  it('initializes language from URL query parameter ?lang=en', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=menu&lang=en']}>
        <CustomerDisplayPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('CART');
    expect(markup).toContain('MENU');
    expect(markup).toContain('HELP');
  });

  it('initializes language from URL query parameter ?lang=vi', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={['/customer-display?preview=menu&lang=vi']}>
        <CustomerDisplayPage />
      </MemoryRouter>,
    );

    expect(markup).toContain('GIỎ HÀNG');
    expect(markup).toContain('THỰC ĐƠN');
    expect(markup).toContain('TRỢ GIÚP');
  });
});

describe('PaymentQrView payment window', () => {
  const qr = 'data:image/png;base64,iVBORw0KGgo=';

  it('counts down the fifteen minutes the merchant allows', () => {
    const markup = renderToStaticMarkup(
      <PaymentQrView
        qr_code={qr}
        order_id="order-1"
        status="pending"
        total={45000}
        created_at={new Date(Date.now() - 5 * 60_000).toString()}
      />,
    );

    expect(markup).toContain('data-testid="payment-countdown"');
    expect(markup).toMatch(/(10:00|09:5\d)/);
    expect(markup).toContain('<img');
  });

  it('withdraws an expired QR so nobody pays a cancelled order', () => {
    const markup = renderToStaticMarkup(
      <PaymentQrView
        qr_code={qr}
        order_id="order-1"
        status="pending"
        total={45000}
        uiLanguage="vi"
        created_at={new Date(Date.now() - 20 * 60_000).toString()}
      />,
    );

    expect(markup).toContain('HẾT THỜI GIAN THANH TOÁN');
    expect(markup).not.toContain('<img');
  });
});

describe('BillView paid order', () => {
  it('shows the order code with a paid status in Vietnamese', () => {
    const markup = renderToStaticMarkup(
      <BillView order_id="ORD-77" branch="ba9355f797" order_type="take-out" status="paid" lines={[]} total={45000} uiLanguage="vi" />,
    );

    expect(markup).toContain('ORD-77');
    expect(markup).toContain('TRẠNG THÁI: ĐÃ THANH TOÁN');
  });
});

