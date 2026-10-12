export const KIOSK_THEMES = {
  playful: {
    label: 'Playful',
    tone: 'light',
    background: 'radial-gradient(ellipse at 45% 40%, #80e9de 0%, transparent 52%), radial-gradient(ellipse at 100% 0%, #aaa0ed 0%, transparent 65%), linear-gradient(125deg, #bdc3f4, #b6eee8 50%, #d9c8f0)',
  },
  'soft-gradient': {
    label: 'Soft Gradient',
    tone: 'light',
    background: 'radial-gradient(ellipse at 70% 85%, #c8a0e6 0%, transparent 65%), radial-gradient(ellipse at 95% 15%, #ecdaf5 0%, transparent 55%), linear-gradient(140deg, #fcfafc 12%, #efe8f4)',
  },
  'purple-glow': {
    label: 'Purple Glow',
    tone: 'dark',
    background: 'radial-gradient(ellipse at 50% 110%, #713cce 0%, #302052 30%, transparent 65%), linear-gradient(145deg, #14121c, #100e18)',
  },
  'midnight-blue': {
    label: 'Midnight Blue',
    tone: 'dark',
    background: 'radial-gradient(ellipse at 50% 60%, #1c326e 0%, #132146 30%, transparent 66%), linear-gradient(150deg, #111317, #0d1017)',
  },
  'pastel-grid': {
    label: 'Pastel',
    tone: 'light',
    background: 'radial-gradient(ellipse at 80% 42%, #b9afd5 0%, transparent 60%), radial-gradient(ellipse at 20% 95%, #c5dfe1 0%, transparent 65%), linear-gradient(125deg, #edf0ef, #cecae9)',
  },
} as const;

export type KioskTheme = keyof typeof KIOSK_THEMES;

export function isKioskTheme(value: unknown): value is KioskTheme {
  return typeof value === 'string' && Object.keys(KIOSK_THEMES).includes(value);
}
