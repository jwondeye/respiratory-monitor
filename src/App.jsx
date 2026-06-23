import { useState, useEffect, useRef, useCallback } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'

const WS_URL = 'ws://192.168.1.232:8000/ws'
const MAX_HISTORY = 30          // 30 × 2 s = 60 seconds of history
const RECONNECT_DELAY_MS = 3000

// ── Icons ────────────────────────────────────────────────────────────────────

function HeartIcon({ style, className }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 21.593c-5.63-5.539-11-10.297-11-14.402 0-3.791
               3.068-5.191 5.281-5.191 1.312 0 4.151.501 5.719 4.457
               1.59-3.968 4.464-4.447 5.726-4.447 2.54 0 5.274 1.621
               5.274 5.181 0 4.069-5.136 8.625-11 14.402z" />
    </svg>
  )
}

function WaveIcon({ className, style }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="1,12 4,12 6,4 9,20 12,12 14,17 16,9 18,12 23,12" />
    </svg>
  )
}

function ActivityIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  )
}

// ── Chart tooltip ─────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-slate-800/95 border border-slate-600 rounded-xl p-3 shadow-2xl backdrop-blur-sm">
      <p className="text-slate-400 text-xs mb-2 font-mono tracking-wide">{label}</p>
      {payload.map(entry => (
        <div key={entry.name} className="flex items-center gap-2 py-0.5">
          <span className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: entry.color }} />
          <span className="text-slate-300 text-xs w-20">{entry.name}</span>
          <span className="font-bold text-sm tabular-nums" style={{ color: entry.color }}>
            {entry.value != null ? entry.value.toFixed(1) : '--'}
          </span>
          <span className="text-slate-500 text-xs">
            {entry.name === 'Heart Rate' ? 'BPM' : 'br/min'}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Metric card ───────────────────────────────────────────────────────────────

function MetricCard({ label, value, unit, icon, accentColor, glowColor, iconStyle, note }) {
  return (
    <div className="relative bg-slate-900 rounded-2xl border border-slate-800 p-6
                    flex flex-col overflow-hidden group">
      {/* Ambient glow */}
      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-700
                      pointer-events-none rounded-2xl"
           style={{ background: `radial-gradient(ellipse at 30% 20%, ${glowColor}18 0%, transparent 70%)` }} />
      {/* Always-on subtle gradient */}
      <div className="absolute inset-0 pointer-events-none rounded-2xl"
           style={{ background: `radial-gradient(ellipse at 30% 20%, ${glowColor}0c 0%, transparent 60%)` }} />

      {/* Label row */}
      <div className="flex items-center gap-2 mb-5 z-10">
        {icon}
        <span className="text-xs font-semibold tracking-[0.18em] uppercase text-slate-400">
          {label}
        </span>
      </div>

      {/* Value */}
      <div className="flex items-end gap-4 z-10">
        <span className="text-8xl font-black tabular-nums leading-none tracking-tight"
              style={{ color: value != null ? '#f8fafc' : '#475569' }}>
          {value != null ? Math.round(value) : '--'}
        </span>
        <div className="flex flex-col mb-2">
          <span className="text-base font-semibold" style={{ color: accentColor }}>
            {unit}
          </span>
        </div>
      </div>

      {/* Divider + status note */}
      <div className="mt-5 z-10">
        <div className="h-px w-full mb-3"
             style={{ background: `linear-gradient(to right, ${accentColor}40, transparent)` }} />
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full flex-shrink-0"
               style={{ background: value != null ? accentColor : '#475569' }} />
          <span className="text-xs text-slate-500">
            {value != null ? note : 'Awaiting data…'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const connected = status === 'connected'
  const connecting = status === 'connecting'

  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium
                     transition-all duration-500
                     ${connected
                       ? 'bg-green-950/60 border-green-800 text-green-400'
                       : connecting
                       ? 'bg-yellow-950/60 border-yellow-800 text-yellow-400'
                       : 'bg-red-950/60 border-red-900 text-red-400'}`}>
      <span className="relative flex h-2 w-2">
        {connected && (
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-60" />
        )}
        <span className={`relative inline-flex rounded-full h-2 w-2
                          ${connected ? 'bg-green-400' : connecting ? 'bg-yellow-400' : 'bg-red-500'}`} />
      </span>
      {connected ? 'Connected' : connecting ? 'Connecting…' : 'Disconnected'}
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [heartRate, setHeartRate]   = useState(null)
  const [respRate, setRespRate]     = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [status, setStatus]         = useState('connecting')
  const [history, setHistory]       = useState([])

  const wsRef          = useRef(null)
  const reconnectRef   = useRef(null)
  const mountedRef     = useRef(true)

  const connect = useCallback(() => {
    if (!mountedRef.current) return

    setStatus('connecting')

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      setStatus('connected')
      clearTimeout(reconnectRef.current)
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setStatus('disconnected')
      reconnectRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
    }

    ws.onerror = () => ws.close()

    ws.onmessage = (event) => {
      if (!mountedRef.current) return
      try {
        const data = JSON.parse(event.data)
        // Extract HH:MM:SS from ISO timestamp or fall back to local time
        const time = data.timestamp
          ? data.timestamp.split('T')[1]?.slice(0, 8) ?? data.timestamp
          : new Date().toTimeString().slice(0, 8)

        setHeartRate(data.heart_rate ?? null)
        setRespRate(data.resp_rate ?? null)
        setLastUpdated(time)

        setHistory(prev => {
          const next = [
            ...prev,
            { time, heartRate: data.heart_rate, respRate: data.resp_rate },
          ]
          return next.length > MAX_HISTORY ? next.slice(-MAX_HISTORY) : next
        })
      } catch {
        // malformed frame — ignore
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  // Pulse duration tracks actual heart rate for the icon animation
  const pulseDuration = heartRate ? `${(60 / heartRate).toFixed(2)}s` : '1s'

  // Breathing cycle duration for RR icon
  const breatheDuration = respRate ? `${(60 / respRate).toFixed(2)}s` : '4s'

  const hrNormal = heartRate != null && heartRate >= 60 && heartRate <= 100
  const rrNormal = respRate  != null && respRate  >= 12 && respRate  <= 20

  return (
    <div className="scanline min-h-screen bg-slate-950 text-slate-50 font-sans select-none">
      {/* Subtle top glow bar */}
      <div className="h-px w-full bg-gradient-to-r from-transparent via-cyan-500/30 to-transparent" />

      <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">

        {/* ── Header ── */}
        <header className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ActivityIcon className="w-5 h-5 text-cyan-400" />
            <div>
              <h1 className="text-sm font-bold tracking-[0.25em] uppercase text-slate-200">
                Vital Signs Monitor
              </h1>
              <p className="text-xs text-slate-600 tracking-wider font-mono mt-0.5">
                MAX30102 · Pi Zero 2W
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <StatusBadge status={status} />
            {lastUpdated && (
              <span className="text-slate-600 text-xs font-mono hidden sm:block">
                {lastUpdated}
              </span>
            )}
          </div>
        </header>

        {/* ── Metric cards ── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <MetricCard
            label="Heart Rate"
            value={heartRate}
            unit="BPM"
            accentColor="#f43f5e"
            glowColor="#f43f5e"
            note={hrNormal ? 'Normal sinus rhythm' : heartRate != null ? 'Out of normal range' : ''}
            icon={
              <HeartIcon
                className="w-5 h-5 text-rose-500 flex-shrink-0"
                style={{
                  animation: heartRate
                    ? `heartbeat ${pulseDuration} ease-in-out infinite`
                    : 'none',
                }}
              />
            }
          />

          <MetricCard
            label="Respiration Rate"
            value={respRate}
            unit="br / min"
            accentColor="#22d3ee"
            glowColor="#22d3ee"
            note={rrNormal ? 'Eupnea — normal range' : respRate != null ? 'Out of normal range' : ''}
            icon={
              <WaveIcon
                className="w-5 h-5 text-cyan-400 flex-shrink-0"
                style={{
                  animation: respRate
                    ? `breathe ${breatheDuration} ease-in-out infinite`
                    : 'none',
                }}
              />
            }
          />
        </div>

        {/* ── Trend chart ── */}
        <div className="bg-slate-900 rounded-2xl border border-slate-800 p-5">
          {/* Chart header */}
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold tracking-[0.18em] uppercase text-slate-400">
                Trend
              </span>
              <span className="text-xs text-slate-600">· last 60 s</span>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <span className="block w-5 h-0.5 rounded-full bg-rose-500" />
                <span className="text-xs text-slate-400">HR</span>
                <span className="text-xs text-slate-600 font-mono tabular-nums ml-1">
                  {heartRate != null ? `${heartRate.toFixed(1)} BPM` : '--'}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="block w-5 h-0.5 rounded-full bg-cyan-400" />
                <span className="text-xs text-slate-400">RR</span>
                <span className="text-xs text-slate-600 font-mono tabular-nums ml-1">
                  {respRate != null ? `${respRate.toFixed(1)} br/min` : '--'}
                </span>
              </div>
            </div>
          </div>

          {history.length === 0 ? (
            <div className="h-52 flex flex-col items-center justify-center gap-3">
              <div className="flex gap-1">
                {[0, 1, 2, 3].map(i => (
                  <div key={i} className="w-1 h-6 bg-slate-700 rounded-full animate-pulse"
                       style={{ animationDelay: `${i * 150}ms` }} />
                ))}
              </div>
              <p className="text-slate-600 text-sm">Collecting data…</p>
              <p className="text-slate-700 text-xs">
                First reading in ~30 s (buffer filling)
              </p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={history} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="4 4" stroke="#1e293b" vertical={false} />

                <XAxis
                  dataKey="time"
                  stroke="#1e293b"
                  tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                  tickLine={false}
                  axisLine={false}
                  interval="preserveStartEnd"
                  padding={{ left: 8, right: 8 }}
                />

                {/* Left Y-axis: HR */}
                <YAxis
                  yAxisId="hr"
                  domain={['auto', 'auto']}
                  stroke="#1e293b"
                  tick={{ fill: '#475569', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={30}
                  tickFormatter={v => Math.round(v)}
                />

                {/* Right Y-axis: RR */}
                <YAxis
                  yAxisId="rr"
                  orientation="right"
                  domain={['auto', 'auto']}
                  stroke="#1e293b"
                  tick={{ fill: '#475569', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={30}
                  tickFormatter={v => Math.round(v)}
                />

                <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#334155', strokeWidth: 1 }} />

                <Line
                  yAxisId="hr"
                  type="monotone"
                  dataKey="heartRate"
                  name="Heart Rate"
                  stroke="#f43f5e"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: '#f43f5e', stroke: '#1e293b', strokeWidth: 2 }}
                  connectNulls
                />
                <Line
                  yAxisId="rr"
                  type="monotone"
                  dataKey="respRate"
                  name="Resp Rate"
                  stroke="#22d3ee"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: '#22d3ee', stroke: '#1e293b', strokeWidth: 2 }}
                  connectNulls
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* ── Footer ── */}
        <footer className="flex items-center justify-between text-slate-700 text-xs font-mono px-1">
          <span>ws://192.168.1.232:8000/ws</span>
          <span>
            {status === 'disconnected' && (
              <span className="text-red-800">reconnecting in {RECONNECT_DELAY_MS / 1000} s…</span>
            )}
            {status === 'connected' && lastUpdated && (
              <span>last update {lastUpdated}</span>
            )}
          </span>
        </footer>

      </div>
    </div>
  )
}
