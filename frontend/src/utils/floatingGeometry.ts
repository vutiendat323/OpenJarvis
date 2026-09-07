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

export type ResizeCorner = 'nw' | 'ne' | 'sw' | 'se';

export interface ResizeState {
  startPos: Point;
  startSize: Dimensions;
  corner: ResizeCorner;
}

/**
 * Computes new dimensions and position based on drag delta for a specific corner
 * while strictly preserving aspect ratio and respecting min/max width constraints.
 */
export function computeCornerResize(
  state: ResizeState,
  deltaX: number,
  deltaY: number,
  aspectRatio: number,
  minWidth = 200,
  maxWidth = Number.POSITIVE_INFINITY,
): { position: Point; size: Dimensions } {
  const ratio = aspectRatio > 0 ? aspectRatio : 16 / 9;
  const effectiveMinWidth = minWidth ?? 200;
  const effectiveMaxWidth = maxWidth ?? Number.POSITIVE_INFINITY;

  let deltaWidth = 0;
  switch (state.corner) {
    case 'se':
    case 'ne':
      deltaWidth = deltaX;
      break;
    case 'sw':
    case 'nw':
      deltaWidth = -deltaX;
      break;
  }

  const rawWidth = state.startSize.width + deltaWidth;
  const targetWidth = Math.min(Math.max(rawWidth, effectiveMinWidth), effectiveMaxWidth);
  const newWidth = Math.round(targetWidth);
  const newHeight = Math.round(targetWidth / ratio);

  let newX = state.startPos.x;
  let newY = state.startPos.y;

  switch (state.corner) {
    case 'se':
      // Top-left is fixed
      break;
    case 'sw':
      // Top-right is fixed
      newX = state.startPos.x + (state.startSize.width - newWidth);
      break;
    case 'ne':
      // Bottom-left is fixed
      newY = state.startPos.y + (state.startSize.height - newHeight);
      break;
    case 'nw':
      // Bottom-right is fixed
      newX = state.startPos.x + (state.startSize.width - newWidth);
      newY = state.startPos.y + (state.startSize.height - newHeight);
      break;
  }

  return {
    position: { x: newX, y: newY },
    size: { width: newWidth, height: newHeight },
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
