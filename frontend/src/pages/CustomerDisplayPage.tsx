import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';

import { useAgentEvents, type AgentEvent } from '@/lib/useAgentEvents';
import {
  isSafeQrImageSource,
  reduceCustomerDisplay,
  waitingState,
  type CustomerDisplayLine,
  type CustomerDisplayState,
} from './customerDisplayState';

const vnd = new Intl.NumberFormat('vi-VN');

function money(value: number | undefined): string {
  return `${vnd.format(value ?? 0)}đ`;
}

function LineList({ lines }: { lines: CustomerDisplayLine[] }) {
  return (
    <ul className="grid gap-3">
      {lines.map((line, index) => (
        <li
          key={`${line.name}-${index}`}
          className="grid grid-cols-[1fr_auto] items-baseline gap-4 rounded-2xl border border-white/10 bg-white/[0.055] px-5 py-4"
        >
          <div>
            <div className="font-semibold">
              {line.quantity !== undefined ? `${line.quantity}× ` : ''}{line.name}
            </div>
            {line.size && <div className="mt-1 text-sm text-white/55">{line.size}</div>}
            {line.note && <div className="mt-1 text-sm text-white/55">{line.note}</div>}
          </div>
          {line.line_total !== undefined && (
            <span className="tabular-nums text-white/80">{money(line.line_total)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function Total({ value }: { value: number }) {
  return (
    <div className="mt-6 flex justify-between border-t border-white/15 pt-5 text-xl font-semibold">
      <span>Tổng cộng</span>
      <span className="tabular-nums">{money(value)}</span>
    </div>
  );
}

function DisplayView({ state }: { state: CustomerDisplayState }) {
  if (state.view === 'waiting') {
    return <div className="text-center text-lg text-white/40">Sẵn sàng phục vụ</div>;
  }

  if (state.view === 'menu') {
    return (
      <section className="w-full max-w-4xl">
        <h1 className="mb-6 text-2xl font-semibold text-white/70">Thực đơn</h1>
        <ul className="grid gap-3 md:grid-cols-2">
          {state.items.map((item, index) => (
            <li
              key={item.id ?? `${item.name}-${index}`}
              className={`grid grid-cols-[1fr_auto] items-baseline gap-4 rounded-2xl border border-white/10 bg-white/[0.055] px-5 py-4 ${item.available === false ? 'opacity-45' : ''}`}
            >
              <div>
                <div className="font-semibold">{item.name}</div>
                {item.note && <div className="mt-1 text-sm text-white/55">{item.note}</div>}
                {item.available === false && <div className="mt-1 text-sm text-white/55">Hết hàng</div>}
              </div>
              {item.price !== undefined && (
                <span className="tabular-nums text-white/80">{money(item.price)}</span>
              )}
            </li>
          ))}
        </ul>
      </section>
    );
  }

  if (state.view === 'cart') {
    return (
      <section className="w-full max-w-3xl">
        <h1 className="mb-6 text-2xl font-semibold text-white/70">Đơn của bạn</h1>
        <LineList lines={state.lines} />
        <Total value={state.total} />
      </section>
    );
  }

  if (state.view === 'bill') {
    return (
      <section className="w-full max-w-3xl">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-white/70">Hóa đơn</h1>
            <p className="mt-1 text-sm text-white/45">{state.order_id}</p>
          </div>
          <div className="text-right text-sm text-white/55">
            <div>{state.branch}</div>
            <div>{state.order_type} · {state.status}</div>
          </div>
        </div>
        <LineList lines={state.lines} />
        <Total value={state.total} />
      </section>
    );
  }

  const imageQr = isSafeQrImageSource(state.qr_code);
  return (
    <section className="flex w-full max-w-2xl flex-col items-center text-center">
      <h1 className="text-2xl font-semibold text-white/70">Thanh toán</h1>
      <p className="mt-2 text-sm text-white/45">{state.order_id} · {state.status}</p>
      <p className="mt-1 text-xs text-white/35">{state.payment_slug}</p>
      <div className="mt-8 rounded-3xl border border-white/10 bg-white/[0.055] p-6">
        <div className="mb-4 text-sm font-medium text-white/55">Mã thanh toán</div>
        {imageQr ? (
          <img
            src={state.qr_code}
            alt="Mã thanh toán"
            className="mx-auto max-h-[55vh] max-w-full rounded-2xl bg-white object-contain p-3"
          />
        ) : (
          <div className="max-w-xl select-text break-all font-mono text-base text-white/85">
            {state.qr_code}
          </div>
        )}
      </div>
    </section>
  );
}

export function CustomerDisplayPage() {
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session')?.trim() || undefined;
  const [state, setState] = useState<CustomerDisplayState>(waitingState);

  useEffect(() => setState(waitingState), [sessionId]);

  const handleEvent = useCallback((event: AgentEvent) => {
    if (!sessionId) return;
    setState((current) => reduceCustomerDisplay(current, event, sessionId));
  }, [sessionId]);

  useAgentEvents(undefined, handleEvent, ['display_update'], sessionId);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#14100c] p-8 text-[#f5efe6]">
      {sessionId
        ? <DisplayView state={state} />
        : <div className="text-center text-lg text-white/45">Thiếu phiên hiển thị.</div>}
    </main>
  );
}
