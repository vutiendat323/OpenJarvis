import { describe, it, expect } from 'vitest';
import {
  clampPosition,
  computeCornerResize,
  computeResizeDimensions,
  getInitialPosition,
  type Dimensions,
  type Point,
} from './floatingGeometry';

describe('floatingGeometry', () => {
  describe('clampPosition', () => {
    const size: Dimensions = { width: 320, height: 180 };
    const bounds: Dimensions = { width: 1920, height: 1080 };

    it('keeps position unchanged when within bounds and margin', () => {
      const pos: Point = { x: 500, y: 300 };
      const clamped = clampPosition(pos, size, bounds, 16);
      expect(clamped).toEqual({ x: 500, y: 300 });
    });

    it('clamps to left margin when x is too small', () => {
      const pos: Point = { x: 5, y: 300 };
      const clamped = clampPosition(pos, size, bounds, 16);
      expect(clamped).toEqual({ x: 16, y: 300 });
    });

    it('clamps to top margin when y is too small', () => {
      const pos: Point = { x: 500, y: -10 };
      const clamped = clampPosition(pos, size, bounds, 16);
      expect(clamped).toEqual({ x: 500, y: 16 });
    });

    it('clamps to right margin when x exceeds max allowed coordinate', () => {
      // max X = bounds.width (1920) - size.width (320) - margin (16) = 1584
      const pos: Point = { x: 1700, y: 300 };
      const clamped = clampPosition(pos, size, bounds, 16);
      expect(clamped).toEqual({ x: 1584, y: 300 });
    });

    it('clamps to bottom margin when y exceeds max allowed coordinate', () => {
      // max Y = bounds.height (1080) - size.height (180) - margin (16) = 884
      const pos: Point = { x: 500, y: 950 };
      const clamped = clampPosition(pos, size, bounds, 16);
      expect(clamped).toEqual({ x: 500, y: 884 });
    });

    it('defaults margin to 0 when margin is omitted', () => {
      const pos: Point = { x: -50, y: 1000 };
      // max X = 1920 - 320 = 1600; max Y = 1080 - 180 = 900
      const clamped = clampPosition(pos, size, bounds);
      expect(clamped).toEqual({ x: 0, y: 900 });
    });

    it('clamps to margin when viewport is smaller than size', () => {
      const smallBounds: Dimensions = { width: 200, height: 100 };
      const pos: Point = { x: 100, y: 50 };
      const clamped = clampPosition(pos, size, smallBounds, 16);
      expect(clamped).toEqual({ x: 16, y: 16 });
    });

    it('handles exact boundary fit', () => {
      const exactBounds: Dimensions = { width: 352, height: 212 }; // size (320x180) + 2*margin (16)
      const pos: Point = { x: 200, y: 200 };
      const clamped = clampPosition(pos, size, exactBounds, 16);
      expect(clamped).toEqual({ x: 16, y: 16 });
    });
  });

  describe('computeResizeDimensions', () => {
    it('scales width and height proportionally for 16:9 ratio', () => {
      const result = computeResizeDimensions(320, 160, 16 / 9);
      // targetWidth = 480, height = 480 / (16/9) = 270
      expect(result).toEqual({ width: 480, height: 270 });
    });

    it('decreases dimensions proportionally when deltaX is negative', () => {
      const result = computeResizeDimensions(480, -80, 16 / 9);
      // targetWidth = 400, height = 400 / (16/9) = 225
      expect(result).toEqual({ width: 400, height: 225 });
    });

    it('clamps to default minWidth (200) when shrunk too far', () => {
      const result = computeResizeDimensions(250, -100, 16 / 9);
      // targetWidth = 200, height = Math.round(200 / (16/9)) = 113
      expect(result).toEqual({ width: 200, height: 113 });
    });

    it('respects custom minWidth and maxWidth limits', () => {
      const belowMin = computeResizeDimensions(300, -150, 16 / 9, 250, 600);
      expect(belowMin).toEqual({ width: 250, height: 141 });

      const aboveMax = computeResizeDimensions(500, 200, 16 / 9, 250, 600);
      expect(aboveMax).toEqual({ width: 600, height: 338 });
    });

    it('handles alternative aspect ratios such as 4:3 and 1:1', () => {
      const fourByThree = computeResizeDimensions(400, 80, 4 / 3);
      expect(fourByThree).toEqual({ width: 480, height: 360 });

      const square = computeResizeDimensions(300, 50, 1);
      expect(square).toEqual({ width: 350, height: 350 });
    });

    it('falls back to 16:9 aspect ratio if aspectRatio is zero or negative', () => {
      const zeroRatio = computeResizeDimensions(320, 0, 0);
      expect(zeroRatio).toEqual({ width: 320, height: 180 });

      const negRatio = computeResizeDimensions(320, 0, -1);
      expect(negRatio).toEqual({ width: 320, height: 180 });
    });

    it('rounds non-integer width and height results', () => {
      const result = computeResizeDimensions(320.4, 15.3, 16 / 9);
      // targetWidth = 335.7 -> rounded to 336, height = 335.7 / (16/9) = 188.83 -> 189
      expect(result.width).toBe(Math.round(335.7));
      expect(Number.isInteger(result.width)).toBe(true);
      expect(Number.isInteger(result.height)).toBe(true);
    });
  });

  describe('computeCornerResize', () => {
    const startPos = { x: 300, y: 200 };
    const startSize = { width: 320, height: 180 };

    it('resizes from SE corner keeping top-left fixed', () => {
      const { position, size } = computeCornerResize(
        { startPos, startSize, corner: 'se' },
        80,
        45,
        16 / 9,
      );
      expect(size).toEqual({ width: 400, height: 225 });
      expect(position).toEqual({ x: 300, y: 200 });
    });

    it('resizes from SW corner moving X left and keeping top fixed', () => {
      // Dragging left (-80px) expands width by +80
      const { position, size } = computeCornerResize(
        { startPos, startSize, corner: 'sw' },
        -80,
        45,
        16 / 9,
      );
      expect(size).toEqual({ width: 400, height: 225 });
      expect(position).toEqual({ x: 220, y: 200 }); // 300 + (320 - 400) = 220
    });

    it('resizes from NE corner moving Y up and keeping left fixed', () => {
      // Dragging right (+80px) expands width by +80
      const { position, size } = computeCornerResize(
        { startPos, startSize, corner: 'ne' },
        80,
        -45,
        16 / 9,
      );
      expect(size).toEqual({ width: 400, height: 225 });
      expect(position).toEqual({ x: 300, y: 155 }); // 200 + (180 - 225) = 155
    });

    it('resizes from NW corner moving both X left and Y up', () => {
      // Dragging up-left (-80px) expands width by +80
      const { position, size } = computeCornerResize(
        { startPos, startSize, corner: 'nw' },
        -80,
        -45,
        16 / 9,
      );
      expect(size).toEqual({ width: 400, height: 225 });
      expect(position).toEqual({ x: 220, y: 155 }); // x: 220, y: 155
    });
  });

  describe('getInitialPosition', () => {
    const size: Dimensions = { width: 320, height: 180 };
    const bounds: Dimensions = { width: 1920, height: 1080 };

    it('calculates top-right corner position with default offsets (16px)', () => {
      // x = 1920 - 320 - 16 = 1584, y = 16
      const pos = getInitialPosition(size, bounds);
      expect(pos).toEqual({ x: 1584, y: 16 });
    });

    it('respects custom top and right offsets', () => {
      // x = 1920 - 320 - 24 = 1576, y = 32
      const pos = getInitialPosition(size, bounds, { top: 32, right: 24 });
      expect(pos).toEqual({ x: 1576, y: 32 });
    });

    it('handles partial offset configurations', () => {
      const onlyTop = getInitialPosition(size, bounds, { top: 40 });
      expect(onlyTop).toEqual({ x: 1584, y: 40 });

      const onlyRight = getInitialPosition(size, bounds, { right: 40 });
      expect(onlyRight).toEqual({ x: 1560, y: 16 });
    });

    it('clamps initial position within viewport bounds when viewport is small', () => {
      const smallBounds: Dimensions = { width: 300, height: 150 };
      const pos = getInitialPosition(size, smallBounds);
      // rawX = 300 - 320 - 16 = -36 -> clamped to 0
      // rawY = 16 (within bounds or clamped)
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeGreaterThanOrEqual(0);
    });
  });
});
