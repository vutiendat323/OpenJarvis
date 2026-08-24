import { describe, expect, it } from 'vitest';

import { appRouteMode } from './appRoute';

describe('appRouteMode', () => {
  it('selects the exclusive customer-display shell on first-load paths', () => {
    expect(appRouteMode('/customer-display')).toBe('customer-display');
    expect(appRouteMode('/customer-display/')).toBe('customer-display');
    expect(appRouteMode('/kiosk')).toBe('application');
    expect(appRouteMode('/')).toBe('application');
  });
});
