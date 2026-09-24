import type {
  PetAnimationConfig,
  PetFrameCoord,
  PetManifest,
  PetState,
} from './types';

/**
 * Validates and normalizes raw manifest JSON into a strongly-typed PetManifest.
 */
export function parsePetManifest(json: unknown): PetManifest {
  if (!json || typeof json !== 'object' || Array.isArray(json)) {
    throw new Error('Invalid manifest: expected an object');
  }

  const raw = json as Record<string, unknown>;

  if (typeof raw.name !== 'string' || !raw.name.trim()) {
    throw new Error('Invalid manifest: name must be a non-empty string');
  }

  if (!raw.size || typeof raw.size !== 'object') {
    throw new Error('Invalid manifest: size object is required');
  }

  const rawSize = raw.size as Record<string, unknown>;
  const width = typeof rawSize.width === 'number' ? rawSize.width : 0;
  const height = typeof rawSize.height === 'number' ? rawSize.height : 0;

  if (width <= 0) {
    throw new Error('Invalid manifest: width must be a positive number');
  }
  if (height <= 0) {
    throw new Error('Invalid manifest: height must be a positive number');
  }

  const animations: Record<string, PetAnimationConfig> = {};
  const rawAnimations = (raw.animations && typeof raw.animations === 'object' && !Array.isArray(raw.animations))
    ? (raw.animations as Record<string, unknown>)
    : {};

  for (const [key, value] of Object.entries(rawAnimations)) {
    if (Array.isArray(value)) {
      animations[key] = {
        sequence: value.filter((n): n is number => typeof n === 'number'),
        loop: true,
      };
    } else if (value && typeof value === 'object' && !Array.isArray(value)) {
      const animObj = value as Record<string, unknown>;
      const sequence = Array.isArray(animObj.sequence)
        ? animObj.sequence.filter((n): n is number => typeof n === 'number')
        : [0];
      animations[key] = {
        sequence,
        frameRate: typeof animObj.frameRate === 'number' ? animObj.frameRate : undefined,
        loop: typeof animObj.loop === 'boolean' ? animObj.loop : true,
        holdLastFrame: typeof animObj.holdLastFrame === 'boolean' ? animObj.holdLastFrame : false,
      };
    }
  }

  if (!animations.idle) {
    animations.idle = { sequence: [0], loop: true };
  }

  return {
    name: raw.name,
    size: { width, height },
    scale: typeof raw.scale === 'number' && raw.scale > 0 ? raw.scale : 1,
    defaultFps: typeof raw.defaultFps === 'number' && raw.defaultFps > 0 ? raw.defaultFps : 8,
    spriteUrl: typeof raw.spriteUrl === 'string' ? raw.spriteUrl : undefined,
    columns: typeof raw.columns === 'number' && raw.columns > 0 ? raw.columns : undefined,
    rows: typeof raw.rows === 'number' && raw.rows > 0 ? raw.rows : undefined,
    animations,
  };
}

/**
 * Computes bounding frame coordinates (x, y, width, height) in the sprite sheet for a given state & frame index.
 */
export function getFrameCoordinates(
  manifest: PetManifest,
  state: PetState | string,
  frameIndex: number,
): PetFrameCoord {
  let anim = manifest.animations[state];

  // Fallback chain for missing animation states
  if (!anim) {
    if (state === 'running-left' && manifest.animations['running-right']) {
      anim = manifest.animations['running-right'];
    } else if (state === 'running-right' && manifest.animations['running-left']) {
      anim = manifest.animations['running-left'];
    } else if (manifest.animations.idle) {
      anim = manifest.animations.idle;
    } else {
      const firstKey = Object.keys(manifest.animations)[0];
      anim = firstKey ? manifest.animations[firstKey] : { sequence: [0], loop: true };
    }
  }

  const sequence = anim.sequence.length > 0 ? anim.sequence : [0];
  let spriteIndex: number;

  if (anim.loop === false) {
    if (anim.holdLastFrame) {
      const clamped = Math.max(0, Math.min(frameIndex, sequence.length - 1));
      spriteIndex = sequence[clamped];
    } else {
      const idx = ((frameIndex % sequence.length) + sequence.length) % sequence.length;
      spriteIndex = sequence[idx];
    }
  } else {
    const idx = ((frameIndex % sequence.length) + sequence.length) % sequence.length;
    spriteIndex = sequence[idx];
  }

  const width = manifest.size.width;
  const height = manifest.size.height;

  if (manifest.columns && manifest.columns > 0) {
    const col = spriteIndex % manifest.columns;
    const row = Math.floor(spriteIndex / manifest.columns);
    return {
      x: col * width,
      y: row * height,
      width,
      height,
    };
  }

  return {
    x: spriteIndex * width,
    y: 0,
    width,
    height,
  };
}

/**
 * Convenience helper to generate CSS style properties for sprite clipping.
 */
export function getFrameStyle(
  manifest: PetManifest,
  state: PetState | string,
  frameIndex: number,
): { width: string; height: string; backgroundPosition: string } {
  const coords = getFrameCoordinates(manifest, state, frameIndex);
  return {
    width: `${coords.width}px`,
    height: `${coords.height}px`,
    backgroundPosition: `-${coords.x}px -${coords.y}px`,
  };
}
