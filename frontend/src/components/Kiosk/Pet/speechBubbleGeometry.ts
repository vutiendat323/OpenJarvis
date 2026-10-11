type Size = { width: number; height: number };
type Rect = Size & { x: number; y: number };
export type BubbleTailSide = 'left' | 'right' | 'top' | 'bottom';
export type BubblePosition = 'left' | 'right' | 'top' | 'bottom' | 'top-left' | 'top-right';

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, Math.max(min, max)));

export function layoutSpeechBubble(
  pane: Size,
  pet: Rect,
  bubble: Size,
  preferred: BubblePosition = 'left',
  gap = 14,
) {
  const padding = 20;
  const headX = pet.x + pet.width / 2;
  const headY = pet.y + pet.height / 4;
  let position: BubblePosition = preferred;
  let tailSide: BubbleTailSide;
  let x: number;
  let y: number;
  if (preferred === 'left' && pet.x >= padding + bubble.width + gap) {
    x = pet.x - bubble.width - gap;
    y = headY - bubble.height / 2;
    tailSide = 'right';
  } else if (preferred === 'left' && pane.width - pet.x - pet.width >= padding + bubble.width + gap) {
    position = 'right';
    x = pet.x + pet.width + gap;
    y = headY - bubble.height / 2;
    tailSide = 'left';
  } else {
    position = preferred === 'left' ? 'top' : preferred;
    x = preferred === 'top-left' ? pet.x
      : preferred === 'top-right' ? pet.x + pet.width - bubble.width
        : headX - bubble.width / 2;
    y = pet.y - bubble.height - gap;
    tailSide = 'bottom';
    if (y < padding && pet.y + pet.height + gap + bubble.height <= pane.height - padding) {
      position = 'bottom';
      y = pet.y + pet.height + gap;
      tailSide = 'top';
    }
  }
  x = Math.round(clamp(x, padding, pane.width - padding - bubble.width));
  y = Math.round(clamp(y, padding, pane.height - padding - bubble.height));
  const horizontalTail = tailSide === 'top' || tailSide === 'bottom';
  const tailOffset = Math.round(clamp(horizontalTail ? headX - x : headY - y, 24, (horizontalTail ? bubble.width : bubble.height) - 24));
  return { x, y, position, tailSide, tailOffset };
}
