import { describe, it, expect, beforeEach } from 'vitest';
import { parsePetManifest, getFrameCoordinates, getFrameStyle } from './codexPetAtlas';
import type { PetManifest } from './types';

describe('codexPetAtlas', () => {
  const sampleManifestData = {
    name: 'minty',
    size: { width: 32, height: 32 },
    scale: 2,
    defaultFps: 8,
    spriteUrl: '/pets/minty/sprite.png',
    columns: 4,
    animations: {
      idle: { sequence: [0, 1, 2, 3], frameRate: 6, loop: true },
      'running-left': { sequence: [4, 5, 6, 7], frameRate: 8, loop: true },
      'running-right': { sequence: [8, 9, 10, 11], frameRate: 8, loop: true },
      waving: { sequence: [12, 13], frameRate: 4, loop: true },
      alert: { sequence: [14], loop: false, holdLastFrame: true },
      failed: { sequence: [15], loop: false },
      review: { sequence: [16, 17], loop: true },
    },
  };

  describe('parsePetManifest', () => {
    it('parses a valid complete manifest', () => {
      const manifest = parsePetManifest(sampleManifestData);
      expect(manifest.name).toBe('minty');
      expect(manifest.size).toEqual({ width: 32, height: 32 });
      expect(manifest.scale).toBe(2);
      expect(manifest.defaultFps).toBe(8);
      expect(manifest.spriteUrl).toBe('/pets/minty/sprite.png');
      expect(manifest.columns).toBe(4);
      expect(manifest.animations.idle.sequence).toEqual([0, 1, 2, 3]);
    });

    it('parses minimal valid manifest with defaults', () => {
      const minimal = {
        name: 'simple',
        size: { width: 16, height: 16 },
      };
      const manifest = parsePetManifest(minimal);
      expect(manifest.name).toBe('simple');
      expect(manifest.size).toEqual({ width: 16, height: 16 });
      expect(manifest.scale).toBe(1);
      expect(manifest.defaultFps).toBe(8);
      expect(manifest.animations.idle).toBeDefined();
      expect(manifest.animations.idle.sequence).toEqual([0]);
    });

    it('normalizes sequence shorthand in animations', () => {
      const withShorthand = {
        name: 'shorthand',
        size: { width: 24, height: 24 },
        animations: {
          idle: [0, 1, 2],
          waving: { sequence: [3, 4] },
        },
      };
      const manifest = parsePetManifest(withShorthand);
      expect(manifest.animations.idle.sequence).toEqual([0, 1, 2]);
      expect(manifest.animations.waving.sequence).toEqual([3, 4]);
    });

    it('throws descriptive error on invalid inputs', () => {
      expect(() => parsePetManifest(null)).toThrow(/Invalid manifest/);
      expect(() => parsePetManifest(undefined)).toThrow(/Invalid manifest/);
      expect(() => parsePetManifest('invalid string')).toThrow(/Invalid manifest/);
      expect(() => parsePetManifest({})).toThrow(/name/);
      expect(() => parsePetManifest({ name: 'test' })).toThrow(/size/);
      expect(() => parsePetManifest({ name: 'test', size: { width: 0, height: 32 } })).toThrow(/width/);
      expect(() => parsePetManifest({ name: 'test', size: { width: 32, height: -5 } })).toThrow(/height/);
    });
  });

  describe('getFrameCoordinates', () => {
    let manifest: PetManifest;

    beforeEach(() => {
      manifest = parsePetManifest(sampleManifestData);
    });

    it('computes grid coordinates correctly for single and multi-row frames', () => {
      // 32x32 frames, 4 columns
      // frame 0 -> row 0, col 0 -> (0, 0)
      expect(getFrameCoordinates(manifest, 'idle', 0)).toEqual({
        x: 0,
        y: 0,
        width: 32,
        height: 32,
      });

      // frame 1 (index in sequence is 1 -> sprite frame 1) -> row 0, col 1 -> (32, 0)
      expect(getFrameCoordinates(manifest, 'idle', 1)).toEqual({
        x: 32,
        y: 0,
        width: 32,
        height: 32,
      });

      // running-left sequence is [4, 5, 6, 7]
      // frame 0 -> sprite frame 4 -> row 1, col 0 -> (0, 32)
      expect(getFrameCoordinates(manifest, 'running-left', 0)).toEqual({
        x: 0,
        y: 32,
        width: 32,
        height: 32,
      });

      // running-left frame 1 -> sprite frame 5 -> row 1, col 1 -> (32, 32)
      expect(getFrameCoordinates(manifest, 'running-left', 1)).toEqual({
        x: 32,
        y: 32,
        width: 32,
        height: 32,
      });
    });

    it('loops sequence when loop is true or omitted', () => {
      // idle has 4 frames: [0, 1, 2, 3]
      expect(getFrameCoordinates(manifest, 'idle', 0).x).toBe(0);
      expect(getFrameCoordinates(manifest, 'idle', 3).x).toBe(96); // col 3
      expect(getFrameCoordinates(manifest, 'idle', 4).x).toBe(0);  // loops back to 0
      expect(getFrameCoordinates(manifest, 'idle', 5).x).toBe(32); // loops to 1
    });

    it('handles holdLastFrame when loop is false', () => {
      // alert sequence: [14], holdLastFrame: true
      const alertCoords = getFrameCoordinates(manifest, 'alert', 5);
      // sprite frame 14 -> row 3, col 2 -> x = 64, y = 96
      expect(alertCoords).toEqual({
        x: 64,
        y: 96,
        width: 32,
        height: 32,
      });
    });

    it('handles negative frame index gracefully', () => {
      const coords = getFrameCoordinates(manifest, 'idle', -1);
      expect(coords.width).toBe(32);
      expect(coords.height).toBe(32);
      expect(coords.x).toBeGreaterThanOrEqual(0);
    });

    it('falls back when animation state is missing in manifest', () => {
      const minimalManifest: PetManifest = {
        name: 'partial',
        size: { width: 32, height: 32 },
        columns: 4,
        animations: {
          idle: { sequence: [0, 1] },
          'running-right': { sequence: [2, 3] },
        },
      };

      // running-left is missing -> should fallback to running-right or idle
      const runningLeftCoords = getFrameCoordinates(minimalManifest, 'running-left', 0);
      expect(runningLeftCoords.width).toBe(32);
      expect(runningLeftCoords.height).toBe(32);

      // completely unknown state -> falls back to idle
      const unknownCoords = getFrameCoordinates(minimalManifest, 'unknown' as any, 0);
      expect(unknownCoords).toEqual({
        x: 0,
        y: 0,
        width: 32,
        height: 32,
      });
    });

    it('computes linear coordinates when columns is not specified', () => {
      const linearManifest: PetManifest = {
        name: 'linear',
        size: { width: 20, height: 20 },
        animations: {
          idle: { sequence: [0, 1, 2] },
        },
      };
      // columns defaulted or linear strip
      const frame1 = getFrameCoordinates(linearManifest, 'idle', 1);
      expect(frame1).toEqual({
        x: 20,
        y: 0,
        width: 20,
        height: 20,
      });
    });
  });

  describe('getFrameStyle', () => {
    it('returns CSS properties with negative background position', () => {
      const manifest = parsePetManifest(sampleManifestData);
      const style = getFrameStyle(manifest, 'idle', 1);
      expect(style.width).toBe('32px');
      expect(style.height).toBe('32px');
      expect(style.backgroundPosition).toBe('-32px -0px');
    });
  });
});
