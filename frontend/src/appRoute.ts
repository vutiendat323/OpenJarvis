export type AppRouteMode = 'application' | 'customer-display';

export function appRouteMode(pathname: string): AppRouteMode {
  return pathname === '/customer-display' || pathname === '/customer-display/'
    ? 'customer-display'
    : 'application';
}
