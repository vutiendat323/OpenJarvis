import { describe, expect, it, vi } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  CodexPet,
  DEFAULT_MINTY_MANIFEST,
  calculatePetFrameStep,
} from './CodexPet';
import type { PetManifest } from './types';

const mockCustomManifest: PetManifest = {
  name: 'pixel-fox',
  size: { width: 48, height: 48 },
  scale: 1,
  defaultFps: 10,
  spriteUrl: '/pets/fox/sprite.png',
  columns: 4,
  rows: 4,
  animations: {
    idle: { sequence: [0, 1], frameRate: 2, loop: true },
    'running-right': { sequence: [2, 3, 4], frameRate: 8, loop: true },
    failed: { sequence: [5, 6], frameRate: 4, loop: false, holdLastFrame: true },
  },
};

describe('DEFAULT_MINTY_MANIFEST', () => {
  it('has valid minty pet configuration with 7 core animations', () => {
    expect(DEFAULT_MINTY_MANIFEST.name).toBe('minty');
    expect(DEFAULT_MINTY_MANIFEST.size).toEqual({ width: 32, height: 32 });
    expect(DEFAULT_MINTY_MANIFEST.scale).toBe(2);
    expect(DEFAULT_MINTY_MANIFEST.columns).toBe(4);
    expect(DEFAULT_MINTY_MANIFEST.rows).toBe(7);
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('idle');
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('running-left');
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('running-right');
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('waving');
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('alert');
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('failed');
    expect(DEFAULT_MINTY_MANIFEST.animations).toHaveProperty('review');
  });
});

describe('calculatePetFrameStep', () => {
  it('advances looping animation frame sequence and wraps around', () => {
    const anim = { sequence: [0, 1, 2], loop: true };
    expect(calculatePetFrameStep(0, anim)).toEqual({ nextFrame: 1, isFinished: false });
    expect(calculatePetFrameStep(1, anim)).toEqual({ nextFrame: 2, isFinished: false });
    expect(calculatePetFrameStep(2, anim)).toEqual({ nextFrame: 0, isFinished: false });
  });

  it('holds on last frame for non-looping animation with holdLastFrame=true', () => {
    const anim = { sequence: [10, 11, 12], loop: false, holdLastFrame: true };
    expect(calculatePetFrameStep(0, anim)).toEqual({ nextFrame: 1, isFinished: false });
    expect(calculatePetFrameStep(1, anim)).toEqual({ nextFrame: 2, isFinished: false });
    expect(calculatePetFrameStep(2, anim)).toEqual({ nextFrame: 2, isFinished: true });
  });

  it('handles single-frame sequence gracefully', () => {
    const anim = { sequence: [0], loop: true };
    expect(calculatePetFrameStep(0, anim)).toEqual({ nextFrame: 0, isFinished: false });
  });
});

describe('CodexPet', () => {
  it('renders default minty pet in idle state', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, { state: 'idle' })
    );
    expect(html).toContain('data-testid="codex-pet"');
    expect(html).toContain('data-state="idle"');
    expect(html).toContain('data-dragging="false"');
    expect(html).toContain('image-rendering:pixelated');
    expect(html).toContain('/pets/minty/sprite.png');
  });

  it('renders each of the 7 core states with appropriate data-state', () => {
    const states = [
      'idle',
      'running-left',
      'running-right',
      'waving',
      'alert',
      'failed',
      'review',
    ] as const;

    for (const state of states) {
      const html = renderToStaticMarkup(
        React.createElement(CodexPet, { state })
      );
      expect(html).toContain(`data-state="${state}"`);
    }
  });

  it('applies scale multiplier to container size (32x32 at scale 2 -> 64x64)', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, { scale: 2 })
    );
    expect(html).toContain('width:64px');
    expect(html).toContain('height:64px');
  });

  it('renders custom manifest with custom scale and frame', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        manifest: mockCustomManifest,
        state: 'running-right',
        scale: 1.5,
        frameIndex: 1,
      })
    );
    expect(html).toContain('data-state="running-right"');
    expect(html).toContain('/pets/fox/sprite.png');
    // 48 * 1.5 = 72px
    expect(html).toContain('width:72px');
    expect(html).toContain('height:72px');
  });

  it('applies dragging state and cursor styles', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        isDragging: true,
      })
    );
    expect(html).toContain('data-dragging="true"');
    expect(html).toContain('cursor:grabbing');
  });

  it('applies absolute position transform when position is passed', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        position: { x: 150, y: 300 },
      })
    );
    expect(html).toContain('transform:translate3d(150px, 300px, 0)');
  });

  it('renders speech bubble when speechText is provided', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        speechText: 'Hello from Minty!',
      })
    );
    expect(html).toContain('data-testid="codex-pet-speech-bubble"');
    expect(html).toContain('Hello from Minty!');
  });

  it('renders typing speech bubble when voiceStatus is speaking without text', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        voiceStatus: 'speaking',
      })
    );
    expect(html).toContain('data-testid="codex-pet-speech-bubble"');
    expect(html).toContain('data-testid="speech-bubble-typing"');
  });

  it('renders custom speechBubble node when provided', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        speechBubble: React.createElement('div', { 'data-testid': 'custom-bubble' }, 'Custom!'),
      })
    );
    expect(html).toContain('data-testid="custom-bubble"');
    expect(html).toContain('Custom!');
  });

  it('renders shadow element by default and hides when showShadow is false', () => {
    const htmlWithShadow = renderToStaticMarkup(
      React.createElement(CodexPet, { showShadow: true })
    );
    expect(htmlWithShadow).toContain('data-testid="codex-pet-shadow"');

    const htmlNoShadow = renderToStaticMarkup(
      React.createElement(CodexPet, { showShadow: false })
    );
    expect(htmlNoShadow).not.toContain('data-testid="codex-pet-shadow"');
  });

  it('renders canvas element when renderMode is canvas', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        renderMode: 'canvas',
      })
    );
    expect(html).toContain('<canvas');
    expect(html).toContain('data-testid="codex-pet-canvas"');
  });

  it('falls back safely for unknown state', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        state: 'nonexistent-state' as any,
      })
    );
    expect(html).toContain('data-testid="codex-pet"');
  });

  it('merges custom className and styles', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPet, {
        className: 'my-custom-pet-class',
        style: { zIndex: 99 },
      })
    );
    expect(html).toContain('my-custom-pet-class');
    expect(html).toContain('z-index:99');
  });
});
