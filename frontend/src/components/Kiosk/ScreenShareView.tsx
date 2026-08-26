import { useEffect, useRef } from 'react';
import { useDraggableResizable } from '@/hooks/useDraggableResizable';

export function ScreenShareView({
  stream,
  floating = false,
}: {
  stream: MediaStream | null;
  floating?: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { position, size, isDragging, cardHandlers, getResizeHandleProps } =
    useDraggableResizable({ initialWidth: 380, aspectRatio: 16 / 9 });

  // srcObject is a property, never an attribute — it cannot be set in JSX.
  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = stream;
  }, [stream]);

  if (!floating) {
    return (
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="absolute inset-0 h-full w-full"
        style={{ objectFit: 'contain', background: '#06060f' }}
      />
    );
  }

  return (
    <div
      {...cardHandlers}
      className={`fixed z-25 select-none rounded-2xl overflow-hidden touch-none transition-shadow ${
        isDragging
          ? 'shadow-[0_20px_50px_rgba(0,0,0,0.8)]'
          : 'shadow-[0_12px_36px_rgba(0,0,0,0.6)]'
      }`}
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        width: `${size.width}px`,
        height: `${size.height}px`,
        background: '#06060f',
        border: '1px solid rgba(255, 255, 255, 0.16)',
      }}
    >
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="w-full h-full object-contain pointer-events-none"
      />

      {/* 4 Corner Resize Handles */}
      <div
        {...getResizeHandleProps('nw')}
        className="absolute top-0 left-0 w-7 h-7 cursor-nwse-resize touch-none z-10"
      />
      <div
        {...getResizeHandleProps('ne')}
        className="absolute top-0 right-0 w-7 h-7 cursor-nesw-resize touch-none z-10"
      />
      <div
        {...getResizeHandleProps('sw')}
        className="absolute bottom-0 left-0 w-7 h-7 cursor-nesw-resize touch-none z-10"
      />
      <div
        {...getResizeHandleProps('se')}
        className="absolute bottom-0 right-0 w-7 h-7 cursor-nwse-resize touch-none z-10"
      />
    </div>
  );
}
