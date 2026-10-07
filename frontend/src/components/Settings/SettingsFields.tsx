import type { ComponentProps, ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function SettingInput({ className, style, ...props }: ComponentProps<'input'>) {
  return (
    <input
      {...props}
      className={cn('w-48 px-2 py-1 rounded text-xs', className)}
      style={{
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text)',
        ...style,
      }}
    />
  );
}

export function Section({ title, children, compact = false }: {
  title: string;
  children: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={compact ? 'rounded-xl p-3.5 sm:p-4 flex flex-col' : 'rounded-xl p-5'}
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
    >
      <h3 className={compact ? 'text-xs sm:text-sm font-semibold mb-1' : 'text-sm font-semibold mb-4'} style={{ color: 'var(--color-text)' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

export function SettingRow({ label, description, children, compact = false, last = false, vertical = false, htmlFor }: {
  label: string;
  description?: ReactNode;
  children: ReactNode;
  compact?: boolean;
  last?: boolean;
  vertical?: boolean;
  htmlFor?: string;
}) {
  const title = htmlFor
    ? <label htmlFor={htmlFor}>{label}</label>
    : label;
  return (
    <div
      className={`${compact ? 'py-2.5' : 'py-3'} ${vertical ? 'flex flex-col gap-2' : 'flex items-center justify-between gap-3'}`}
      style={{ borderBottom: last ? 'none' : '1px solid var(--color-border-subtle)' }}
    >
      <div className={vertical ? undefined : 'min-w-0 flex-1'}>
        <div className={compact ? 'text-xs sm:text-sm font-medium leading-snug' : 'text-sm'} style={{ color: 'var(--color-text)' }}>{title}</div>
        {description && (
          <div className={compact ? 'text-[11px] mt-0.5 leading-normal' : 'text-xs mt-0.5'} style={{ color: 'var(--color-text-tertiary)' }}>{description}</div>
        )}
      </div>
      <div className={vertical ? undefined : 'shrink-0 flex items-center'}>{children}</div>
    </div>
  );
}
