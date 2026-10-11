import { describe, expect, it } from 'vitest';
import { layoutSpeechBubble } from './speechBubbleGeometry';

describe('speech bubble placement', () => {
  it('points toward the head from the left when space is available', () => {
    const result = layoutSpeechBubble({ width: 900, height: 600 }, { x: 550, y: 200, width: 200, height: 200 }, { width: 260, height: 100 });
    expect(result.position).toBe('left');
    expect(result.tailSide).toBe('right');
    expect(result.x + 260).toBeLessThan(550);
    expect(result.y + result.tailOffset).toBe(250);
  });
  it('uses the right side when the robot is against the left edge', () => {
    const result = layoutSpeechBubble({ width: 900, height: 600 }, { x: 16, y: 200, width: 200, height: 200 }, { width: 260, height: 100 });
    expect(result.position).toBe('right');
    expect(result.tailSide).toBe('left');
    expect(result.x).toBeGreaterThan(216);
  });
  it('keeps a narrow-pane bubble above the pet with its tail aimed down', () => {
    const result = layoutSpeechBubble({ width: 320, height: 600 }, { x: 30, y: 220, width: 260, height: 260 }, { width: 260, height: 100 });
    expect(result.position).toBe('top');
    expect(result.tailSide).toBe('bottom');
    expect(result.x + result.tailOffset).toBe(160);
    expect(result.x).toBeGreaterThanOrEqual(20);
    expect(result.x + 260).toBeLessThanOrEqual(300);
    expect(result.y + 100).toBeLessThan(220);
  });
  it('moves below a pet near the top without cutting off the frame', () => {
    const result = layoutSpeechBubble({ width: 320, height: 600 }, { x: 30, y: 16, width: 260, height: 260 }, { width: 260, height: 100 });
    expect(result.position).toBe('bottom');
    expect(result.tailSide).toBe('top');
    expect(result.y).toBeGreaterThan(276);
    expect(result.y + 100).toBeLessThanOrEqual(580);
  });
});
