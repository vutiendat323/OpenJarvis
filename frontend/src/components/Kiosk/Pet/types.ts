export type PetState =
  | 'idle'
  | 'running-left'
  | 'running-right'
  | 'waving'
  | 'alert'
  | 'failed'
  | 'review';

export interface PetPosition {
  x: number;
  y: number;
}

export interface PetSize {
  width: number;
  height: number;
}

export interface PetFrameCoord {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PetAnimationConfig {
  sequence: number[];
  frameRate?: number;
  loop?: boolean;
  holdLastFrame?: boolean;
}

export interface PetManifest {
  name: string;
  size: PetSize;
  scale?: number;
  defaultFps?: number;
  spriteUrl?: string;
  columns?: number;
  rows?: number;
  animations: Record<string, PetAnimationConfig>;
}
