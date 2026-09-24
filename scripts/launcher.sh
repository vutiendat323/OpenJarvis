#!/usr/bin/env bash
# OpenJarvis full-stack launcher: Vision (GPU) + Backend + Frontend.
#
# Every export here fixes a failure we actually hit; dropping one breaks a
# feature silently rather than loudly:
#
#   OMP/ORT_NUM_THREADS       unset -> VieNeu renders at 0.4x realtime and
#                             speech breaks up mid-word. Pinned at 4 -> 2.1x.
#                             Must be exported BEFORE the process starts:
#                             numpy sizes its pool at import time.
#   OPENJARVIS_LOCAL_TTS_...  unset -> voice hangs on "Connecting..." forever
#                             (vieneu_artifact_required).
#   OPENJARVIS_CONFIG         unset -> no browser tools; the Agent answers
#                             "no tools for this session".
#   LD_LIBRARY_PATH (vision)  unset -> onnxruntime silently falls back to CPU
#                             and Vision eats ~5 cores instead of the GPU.
#
# Usage: scripts/launcher.sh start|restart|stop|status [--no-vision]
#        scripts/launcher.sh logs [vision|backend|frontend] [-f]
#        scripts/launcher.sh help

set -Eeuo pipefail

# ---------- Standard Linux CLI Logging Helpers ----------
# Format: [YYYY-MM-DD HH:MM:SS] [LEVEL] component: message
# LEVEL is INFO, WARN or ERROR; log_ok is INFO in green, for a check that passed.
_log_msg() {
    local level="$1"
    local color="$2"
    local fd="$3"
    shift 3
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    if [ -t "$fd" ]; then
        printf '[%s] %b[%-5s]%b %s\n' "$ts" "$color" "$level" "\033[0m" "$*" >&"$fd"
    else
        printf '[%s] [%-5s] %s\n' "$ts" "$level" "$*" >&"$fd"
    fi
}

log_info()  { _log_msg "INFO"  "\033[36m" 1 "$@"; }   # Cyan
log_ok()    { _log_msg "INFO"  "\033[32m" 1 "$@"; }   # Green
log_warn()  { _log_msg "WARN"  "\033[33m" 1 "$@"; }   # Yellow
log_error() { _log_msg "ERROR" "\033[31m" 2 "$@"; }   # Red

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${OPENJARVIS_ENV_FILE:-/home/robber/Work/jarvis/OpenJarvis/.env}"
VISION_DIR="${OPENJARVIS_VISION_DIR:-/home/robber/Work/jarvis/vision}"
ARTIFACT_DIR="${OPENJARVIS_LOCAL_TTS_ARTIFACT_DIR:-$HOME/.cache/openjarvis/vieneu-3.2.3-onnx}"
# The kiosk config is the default because it is what this stack is run for:
# it gives the Agent the http_request + display_* tools the /kiosk route
# drives. The browser-agent config is still one env var away.
MCP_CONFIG="${OPENJARVIS_CONFIG:-configs/openjarvis/examples/ordering-kiosk-mcp.toml}"
MODEL="${OPENJARVIS_MODEL:-openrouter/openai/gpt-5.6-luna}"
# VieNeu ONNX thread pools.  More threads buy no audible speed here: measured
# on this box (i7-11800H, otherwise idle) across four utterance lengths,
# 1 thread renders at RTF 0.34 on 2.99 cores while 4 threads render at RTF
# 0.33 on 5.93 cores.  Both finish ~3x faster than playback, so the extra
# ~2.9 cores buy a difference nobody can hear -- and on a kiosk those cores
# are contended by vision and the display browser.
#
# This is a per-machine tuning value, not a universal one: tts_engine.py
# records a box where more threads did help.  Re-measure on new hardware and
# override with OPENJARVIS_VIENEU_THREADS rather than editing this default.
THREADS="${OPENJARVIS_VIENEU_THREADS:-1}"

LOG_DIR="${OPENJARVIS_LOG_DIR:-/tmp/openjarvis-stack}"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
VISION_LOG="$LOG_DIR/vision.log"
# Backend and frontend are found by their ports.  Vision opens :9876 only
# after its models load, so it gets a pid file written at launch.
VISION_PID_FILE="$LOG_DIR/vision.pid"

usage() {
    cat <<'EOF'
Usage: scripts/launcher.sh <command> [options]

Commands:
  start     Stop any running stack, then start vision, backend and frontend
  restart   Same as start
  stop      Stop every stack service
  status    Show each service's process, uptime and health
  logs      Show service logs: logs [vision|backend|frontend] [-f]
  help      Show this help

Options:
  --no-vision   Leave the vision service out (start/restart/stop/status)
  -f            Follow the logs (logs only)
EOF
}

WITH_VISION=1
ACTION="start"
LOG_SERVICE=""
FOLLOW=0
for arg in "$@"; do
    case "$arg" in
        start|restart|stop|status|logs) ACTION="$arg" ;;
        help|-h|--help) usage; exit 0 ;;
        --no-vision) WITH_VISION=0 ;;
        -f|--follow) FOLLOW=1 ;;
        vision|backend|frontend)
            [ "$ACTION" = logs ] || { log_error "args: '$arg' only goes with logs"; exit 2; }
            LOG_SERVICE="$arg" ;;
        *) log_error "args: unknown argument '$arg'"; usage >&2; exit 2 ;;
    esac
done
[ "$ACTION" = restart ] && ACTION=start

http_ok() { curl -fsS -o /dev/null --max-time 2 "$1" 2>/dev/null; }

# PID from a pid file, or nothing when the file is missing or the process is gone.
pidfile_pid() {
    local pid
    pid="$(cat "$1" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && echo "$pid"
    return 0
}

# Keep the previous run's log as *.log.1, so a restart never erases the log of
# the crash that made you restart; start the new one with a timestamped header.
fresh_log() {
    [ -f "$1" ] && mv -f "$1" "$1.1"
    printf '[%s] [launcher] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$2" > "$1"
}

# -sTCP:LISTEN matters: a bare `lsof -ti:PORT` also returns *clients* connected
# to the port, so an open browser tab shows up as the server — and stop would
# kill the browser instead of the stack.
port_pid() { lsof -ti:"$1" -sTCP:LISTEN 2>/dev/null || true; }

# SIGTERM (never -9): the backend needs to reap its Playwright MCP child,
# otherwise orphaned `npx @playwright/mcp` processes pile up across restarts.
stop_stack() {
    local pid doomed=() remaining deadline targets=""
    for port in 8000 5173; do
        pid="$(port_pid "$port")"
        if [ -n "$pid" ]; then
            targets="$targets, :$port $pid"
            kill "$pid" 2>/dev/null || true
            # Track the PIDs, not the ports.  A backend that has released its
            # listener but not yet exited still holds ~700 MB, and the old
            # port-based sweep could not see it -- so it survived the restart
            # and ran alongside its replacement.
            doomed+=("$pid")
        fi
    done
    if [ "$WITH_VISION" = 1 ]; then
        local v_pids
        v_pids="$(pidfile_pid "$VISION_PID_FILE")"
        [ -n "$v_pids" ] || v_pids="$(pgrep -f "^python3 main\.py$" 2>/dev/null || true)"
        if [ -n "$v_pids" ]; then
            targets="$targets, vision $(echo $v_pids)"
            for vp in $v_pids; do
                kill "$vp" 2>/dev/null || true
                doomed+=("$vp")
            done
        fi
    fi

    if [ -n "$targets" ]; then
        log_info "stop: SIGTERM ${targets#, }"
    else
        log_info "stop: nothing running"
    fi

    # Wait for a clean exit rather than a fixed sleep: SIGTERM has to give the
    # backend time to reap its Playwright MCP child.
    deadline=$((SECONDS + 15))
    while [ "$SECONDS" -lt "$deadline" ]; do
        remaining=""
        for pid in "${doomed[@]:-}"; do
            [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && remaining="$remaining $pid"
        done
        [ -z "$remaining" ] && break
        sleep 0.5
    done

    # Anything the backend failed to reap.
    local mcp_pids
    mcp_pids="$(pgrep -f '@playwright/mcp|playwright-mcp' 2>/dev/null || true)"
    if [ -n "$mcp_pids" ]; then
        log_info "stop: reaping orphaned Playwright MCP"
        pkill -9 -f "@playwright/mcp" 2>/dev/null || true
        pkill -9 -f "playwright-mcp" 2>/dev/null || true
    fi

    # A SIGTERM-exited backend can leave its separately launched Chrome alive.
    # Only stop the process named by this kiosk profile's Chromium lock.
    local browser_profile="$ROOT_DIR/.openjarvis/ordering-kiosk/shared-browser-profile"
    local browser_lock="$browser_profile/SingletonLock"
    local browser_owner browser_pid browser_cmdline browser_active
    if [ -L "$browser_lock" ]; then
        browser_owner="$(readlink "$browser_lock")"
        browser_pid="${browser_owner##*-}"
        browser_active=0
        if [[ "$browser_pid" =~ ^[0-9]+$ ]] && [ -r "/proc/$browser_pid/cmdline" ]; then
            browser_cmdline="$(tr '\0' ' ' < "/proc/$browser_pid/cmdline")"
            if [[ "$browser_cmdline" == *chrome* || "$browser_cmdline" == *chromium* ]] &&
               [[ "$browser_cmdline" == *"--user-data-dir=.openjarvis/ordering-kiosk/shared-browser-profile"* ||
                  "$browser_cmdline" == *"--user-data-dir=$browser_profile"* ]]; then
                log_info "stop: orphaned shared browser $browser_pid"
                kill "$browser_pid" 2>/dev/null || true
                for _ in {1..10}; do
                    kill -0 "$browser_pid" 2>/dev/null || break
                    sleep 0.5
                done
                if kill -0 "$browser_pid" 2>/dev/null; then
                    browser_active=1
                    log_warn "stop: shared browser $browser_pid still running, keeping its profile lock"
                fi
            fi
        elif [[ "$browser_pid" =~ ^[0-9]+$ ]] && kill -0 "$browser_pid" 2>/dev/null; then
            browser_active=1
            log_warn "stop: cannot inspect shared browser $browser_pid, keeping its profile lock"
        fi
        # Chromium leaves these symlinks behind after an unclean exit. A dead
        # lock PID (or a PID reused by an unrelated process) cannot own this
        # profile, and the next Chrome launch otherwise exits before CDP starts.
        if [ "$browser_active" -eq 0 ] && [ -L "$browser_lock" ] &&
           [ "$(readlink "$browser_lock")" = "$browser_owner" ]; then
            for singleton in SingletonLock SingletonCookie SingletonSocket; do
                if [ -L "$browser_profile/$singleton" ]; then
                    unlink "$browser_profile/$singleton"
                fi
            done
        fi
    fi

    # Whatever ignored SIGTERM, by PID and by port.
    for pid in "${doomed[@]:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log_warn "stop: SIGKILL $pid (ignored SIGTERM)"
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    for port in 8000 5173; do
        pid="$(port_pid "$port")"
        if [ -n "$pid" ]; then
            log_warn "stop: SIGKILL :$port $pid"
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    [ "$WITH_VISION" = 0 ] || rm -f "$VISION_PID_FILE"
    log_ok "stop: done"
    return 0
}

# One status row: SERVICE STATE PID PORT UPTIME HEALTH.
status_row() {
    local name="$1" pid="$2" port="$3" health="$4" state uptime
    if [ -n "$pid" ]; then
        state="running"
        uptime="$(ps -o etime= -p "${pid%% *}" 2>/dev/null | tr -d ' ' || true)"
    else
        state="stopped"; pid="-"; uptime="-"; health="-"
    fi
    printf '%-9s %-8s %-7s %-5s %-11s %s\n' "$name" "$state" "$pid" "$port" "${uptime:--}" "$health"
}

# "ok" when every URL answers 200, else the first one that does not.
health_of() {
    local url
    for url in "$@"; do
        http_ok "$url" || { echo "FAIL ${url#http://127.0.0.1}"; return 0; }
    done
    echo "ok"
}

status_stack() {
    local v_pid b_pid f_pid mcp_count
    v_pid="$(pidfile_pid "$VISION_PID_FILE")"
    [ -n "$v_pid" ] || v_pid="$(pgrep -f '^python3 main\.py$' 2>/dev/null | tr '\n' ' ' | sed 's/ *$//' || true)"
    b_pid="$(port_pid 8000)"
    f_pid="$(port_pid 5173)"
    mcp_count="$(pgrep -f '@playwright/mcp' 2>/dev/null | wc -l)"

    printf '%-9s %-8s %-7s %-5s %-11s %s\n' SERVICE STATE PID PORT UPTIME HEALTH
    if [ "$WITH_VISION" = 1 ]; then
        status_row vision "$v_pid" 9876 "$(health_of http://127.0.0.1:9876/)"
    else
        printf '%-9s %s\n' vision "disabled (--no-vision)"
    fi
    status_row backend "$b_pid" 8000 \
        "$(health_of http://127.0.0.1:8000/health http://127.0.0.1:8000/api/kiosk/state)"
    status_row frontend "$f_pid" 5173 \
        "$(health_of http://127.0.0.1:5173/kiosk http://127.0.0.1:5173/customer-display)"
    printf '%-9s %s active Playwright process(es)\n' mcp "$mcp_count"
}

logs_stack() {
    local files=()
    case "$LOG_SERVICE" in
        vision)   files=("$VISION_LOG") ;;
        backend)  files=("$BACKEND_LOG") ;;
        frontend) files=("$FRONTEND_LOG") ;;
        *)        files=("$VISION_LOG" "$BACKEND_LOG" "$FRONTEND_LOG") ;;
    esac
    if [ "$FOLLOW" = 1 ]; then
        exec tail -n 50 -F "${files[@]}"
    fi
    local f
    for f in "${files[@]}"; do
        [ -f "$f" ] || { log_warn "logs: no log yet at $f"; continue; }
        [ "${#files[@]}" -gt 1 ] && printf '==> %s <==\n' "$f"
        tail -n 50 "$f"
    done
}

case "$ACTION" in
    stop)   stop_stack; exit 0 ;;
    status) status_stack; exit 0 ;;
    logs)   logs_stack; exit 0 ;;
esac

# ---------- preflight: fail loudly now, not silently at runtime ----------
[ -x "$ROOT_DIR/.venv/bin/jarvis" ] || { log_error "preflight: missing $ROOT_DIR/.venv/bin/jarvis"; exit 1; }
[ -f "$ENV_FILE" ]                  || { log_error "preflight: missing env file $ENV_FILE"; exit 1; }
[ -d "$ARTIFACT_DIR" ]              || { log_error "preflight: missing VieNeu artifact $ARTIFACT_DIR"; exit 1; }
case "$MCP_CONFIG" in
    /*) MCP_CONFIG_PATH="$MCP_CONFIG" ;;
    *)  MCP_CONFIG_PATH="$ROOT_DIR/$MCP_CONFIG" ;;
esac
[ -f "$MCP_CONFIG_PATH" ]           || { log_error "preflight: missing config $MCP_CONFIG"; exit 1; }

set -a
. "$ENV_FILE"
set +a
[ -n "${GEMINI_API_KEY:-}" ]   || { log_error "preflight: GEMINI_API_KEY missing in $ENV_FILE (voice STT needs it)"; exit 1; }
[ -n "${DEEPSEEK_API_KEY:-}" ] || { log_error "preflight: DEEPSEEK_API_KEY missing in $ENV_FILE"; exit 1; }

log_ok "preflight: ok (model=$MODEL, vision=$WITH_VISION, threads=$THREADS)"
mkdir -p "$LOG_DIR"
stop_stack

# ---------- Vision (GPU) ----------
if [ "$WITH_VISION" = 1 ] && [ -d "$VISION_DIR" ]; then
    # onnxruntime-gpu ships no CUDA libs; the nvidia-* wheels do. Without these
    # on the path it prints a load error and quietly runs on CPU.
    NV_ROOT="$(python3 -c 'import nvidia,os;print(os.path.dirname(nvidia.__file__))' 2>/dev/null || true)"
    VISION_LD=""
    [ -n "$NV_ROOT" ] && VISION_LD="$NV_ROOT/cu13/lib:$NV_ROOT/cudnn/lib"
    # setsid, not `& disown`: disown is a no-op in a non-interactive subshell,
    # which then blocks in wait() and the launcher never returns.
    fresh_log "$VISION_LOG" "vision: python3 main.py (cwd $VISION_DIR)"
    # cd first, then a bare `setsid ... &`: that child execs straight through
    # setsid and env into python, so $! is the vision PID itself.
    (cd "$VISION_DIR" || exit 1
     setsid env LD_LIBRARY_PATH="$VISION_LD:${LD_LIBRARY_PATH:-}" \
        python3 main.py >>"$VISION_LOG" 2>&1 </dev/null &
     echo $! >"$VISION_PID_FILE")
fi

# ---------- Backend ----------
fresh_log "$BACKEND_LOG" "backend: jarvis serve --model $MODEL (config $MCP_CONFIG)"
(cd "$ROOT_DIR" || exit 1
 setsid env \
    OMP_NUM_THREADS="$THREADS" \
    ORT_NUM_THREADS="$THREADS" \
    OPENJARVIS_VIENEU_THREADS="$THREADS" \
    KIOSK_ENABLED=true \
    KIOSK_VISION_URL=ws://127.0.0.1:9876 \
    OPENJARVIS_LOCAL_TTS_ARTIFACT_DIR="$ARTIFACT_DIR" \
    OPENJARVIS_CONFIG="$MCP_CONFIG" \
    .venv/bin/jarvis serve --host 127.0.0.1 --port 8000 --engine cloud --model "$MODEL" \
    >>"$BACKEND_LOG" 2>&1 </dev/null &)

# ---------- Frontend ----------
fresh_log "$FRONTEND_LOG" "frontend: npm run dev (vite :5173)"
(cd "$ROOT_DIR/frontend" || exit 1
 setsid npm run dev -- \
    --host 127.0.0.1 --port 5173 --strictPort \
    >>"$FRONTEND_LOG" 2>&1 </dev/null &)
started="backend, frontend"
[ "$WITH_VISION" = 1 ] && started="vision $(cat "$VISION_PID_FILE"), $started"
log_info "start: $started (logs $LOG_DIR, timeout 90s)"

# ---------- Healthcheck ----------
backend_up=0
frontend_up=0
# Vision is not waited on when disabled; the loop treats it as already up.
vision_up=$((1 - WITH_VISION))
for elapsed in $(seq 1 90); do
    if [ "$vision_up" -eq 0 ] && http_ok http://127.0.0.1:9876/; then
        vision_up=1
        if grep -qiE 'Failed to create CUDAExecutionProvider|libcublasLt' "$VISION_LOG" 2>/dev/null; then
            log_warn "vision: serving :9876 (${elapsed}s) on CPU fallback, check nvidia-* wheels"
        else
            log_ok "vision: serving :9876 (${elapsed}s)"
        fi
    fi
    if [ "$backend_up" -eq 0 ] && curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        backend_up=1
        log_ok "backend: healthy :8000 (${elapsed}s)"
    fi
    if [ "$frontend_up" -eq 0 ] && curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
        frontend_up=1
        log_ok "frontend: ready :5173 (${elapsed}s)"
    fi
    if [ "$backend_up" -eq 1 ] && [ "$frontend_up" -eq 1 ] && [ "$vision_up" -eq 1 ]; then
        break
    fi

    # Early exit check: fail fast if backend crashed on startup
    if [ "$backend_up" -eq 0 ] && [ "$elapsed" -ge 4 ] && [ -z "$(port_pid 8000)" ]; then
        if grep -qiE 'Traceback \(most recent call last\)|Error:|Exception:' "$BACKEND_LOG" 2>/dev/null; then
            log_error "backend: exited with an error, last lines of $BACKEND_LOG:"
            tail -n 12 "$BACKEND_LOG" >&2 || true
            exit 1
        fi
    fi
    sleep 1
done

if [ "$backend_up" -eq 0 ]; then
    log_error "backend: not healthy after 90s, see $BACKEND_LOG"
    exit 1
fi
if [ "$frontend_up" -eq 0 ]; then
    log_error "frontend: not ready after 90s, see $FRONTEND_LOG"
    exit 1
fi
# Vision stays a warning, as before: the kiosk still runs without presence.
if [ "$vision_up" -eq 0 ]; then
    log_warn "vision: not serving :9876 after 90s, see $VISION_LOG"
fi

# ---------- Kiosk routes ----------
kiosk="" kiosk_bad=0
for route in /kiosk /customer-display; do
    if http_ok "http://127.0.0.1:5173$route"; then kiosk="$kiosk, $route ok"
    else kiosk="$kiosk, $route FAIL"; kiosk_bad=1; fi
done
case "$(curl -fsS --max-time 2 http://127.0.0.1:8000/api/kiosk/state 2>/dev/null || true)" in
    *'"running":true'*) kiosk="$kiosk, runtime running" ;;
    *)                  kiosk="$kiosk, runtime NOT running"; kiosk_bad=1 ;;
esac
if [ "$kiosk_bad" -eq 0 ]; then log_ok "kiosk: ${kiosk#, }"; else log_warn "kiosk: ${kiosk#, }"; fi

# ---------- Agent ----------
tools=$(grep -o 'Agent tools:.*' "$BACKEND_LOG" 2>/dev/null | tr ',' '\n' | grep -c 'browser_' || true)
expected_tools=$("$ROOT_DIR/.venv/bin/python" -c '
import sys, tomllib
with open(sys.argv[1], "rb") as stream:
    value = tomllib.load(stream).get("tools", {}).get("enabled", "")
names = value if isinstance(value, list) else value.split(",")
print(sum(str(name).strip().startswith("browser_") for name in names))
' "$MCP_CONFIG_PATH" 2>/dev/null || echo 0)

mcp=$(pgrep -f '@playwright/mcp' 2>/dev/null | wc -l)
mem=$(curl -fsS http://127.0.0.1:8000/v1/memory/config 2>/dev/null || echo '')
agent="browser tools $tools/$expected_tools, playwright mcp $mcp"
agent_bad=0
[ "$tools" -eq "$expected_tools" ] && [ "$mcp" -eq 1 ] || agent_bad=1
case "$mem" in
    *'"available":true'*) agent="$agent, memory ok" ;;
    *) agent="$agent, memory unavailable (uv run maturin develop -m rust/crates/openjarvis-python/Cargo.toml --release)"
       agent_bad=1 ;;
esac
if [ "$agent_bad" -eq 0 ]; then log_ok "agent: $agent"; else log_warn "agent: $agent"; fi

urls="kiosk http://127.0.0.1:5173/kiosk | display http://127.0.0.1:5173/customer-display | chat http://127.0.0.1:5173"
[ "$WITH_VISION" = 1 ] && urls="$urls | vision http://127.0.0.1:9876/"
log_ok "ready: $urls"
