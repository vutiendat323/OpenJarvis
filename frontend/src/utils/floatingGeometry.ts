export interface Point {
  x: number;
  y: number;
}

export interface Dimensions {
  width: number;
  height: number;
}

/**
 * Clamps a 2D position within the specified bounds, ensuring the entire
 * element fits within [margin, bounds.dimension - size.dimension - margin].
 * If the viewport/bounds is smaller than size, clamps to margin.
 */
export function clampPosition(
  pos: Point,
  size: Dimensions,
  bounds: Dimensions,
  margin = 0,
): Point {
  const minX = margin;
  const maxX = Math.max(margin, bounds.width - size.width - margin);
  const clampedX = Math.min(Math.max(pos.x, minX), maxX);

  const minY = margin;
  const maxY = Math.max(margin, bounds.height - size.height - margin);
  const clampedY = Math.min(Math.max(pos.y, minY), maxY);

  return {
    x: clampedX,
    y: clampedY,
  };
}

/**
 * Computes new dimensions based on a horizontal drag delta while strictly
 * preserving aspect ratio and respecting min/max width constraints.
 */
export function computeResizeDimensions(
  startWidth: number,
  deltaX: number,
  aspectRatio: number,
  minWidth = 200,
  maxWidth = Number.POSITIVE_INFINITY,
): Dimensions {
  const ratio = aspectRatio > 0 ? aspectRatio : 16 / 9;
  const effectiveMinWidth = minWidth ?? 200;
  const effectiveMaxWidth = maxWidth ?? Number.POSITIVE_INFINITY;

  const rawWidth = startWidth + deltaX;
  const targetWidth = Math.min(Math.max(rawWidth, effectiveMinWidth), effectiveMaxWidth);

  const width = Math.round(targetWidth);
  const height = Math.round(targetWidth / ratio);

  return { width, height };
}

/**
 * Computes the initial default position for a floating window (typically top-right corner),
 * clamped within the bounding viewport.
 */
export function getInitialPosition(
  size: Dimensions,
  bounds: Dimensions,
  offset?: { top?: number; right?: number },
): Point {
  const top = offset?.top ?? 16;
  const right = offset?.right ?? 16;

  const targetX = bounds.width - size.width - right;
  const targetY = top;

  return clampPosition({ x: targetX, y: targetY }, size, bounds, 0);
}
