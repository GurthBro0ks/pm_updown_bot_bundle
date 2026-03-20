// SignalsTab.jsx — Real-time signal cascade view
// API: http://NUC_IP:8510/api/signals/current + /api/signals/history
//
// Matches cyberpunk aesthetic: dark (#060a0e), accent #00ff9d, warning #ffbe0b, error #ff4757
// Font: JetBrains Mono for data, DM Sans for labels
// Sparkline overlay + scanline consistent with other tabs

import React, { useState, useEffect, useCallback } from 'react';

// ─── Mock data for development ────────────────────────────────────────────────
const MOCK_SIGNALS = {
  timestamp: new Date().toISOString(),
  collection_time_ms: 42.0,
  sources: {
    gdelt: {
      status: "ok",
      geo_risk_score: 0.42,
      event_count: 23,
      avg_tone: -0.15,
      top_events: [
        { title: "Military exercise near Taiwan Strait enters third day", tone: -0.3, kw_multiplier: 1.4, regions: ["geopolitical_taiwan"] },
        { title: "EU sanctions package vote delayed amid divisions", tone: -0.2, kw_multiplier: 1.2, regions: ["geopolitical_europe"] },
        { title: "Ceasefire negotiations resume in closed-door format", tone: 0.1, kw_multiplier: 0.7, regions: ["geopolitical_mideast"] },
      ],
      regions: { geopolitical_taiwan: 3, geopolitical_europe: 2, geopolitical_mideast: 1 },
      last_updated: new Date().toISOString(),
      cached: false,
    },
    grok: {
      status: "ok",
      model: "grok_420",
      last_sentiment: 0.71,
      last_market: "FED-26.JUN",
      last_updated: new Date().toISOString(),
    },
    glm: {
      status: "stale",
      model: "glm",
      last_sentiment: 0.68,
      last_market: "TSLA-earnings",
      last_updated: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
    },
    stock_hunter: {
      status: "ok",
      active_tickers: 5,
      last_scan_time: new Date(Date.now() - 15 * 60 * 1000).toISOString(),
      positions_open: 1,
      news_sources_active: ["finnhub", "alpha_vantage"],
      top_signals: [
        { ticker: "NVDA", sentiment: 0.82, source: "alpha_vantage", passes_threshold: true },
        { ticker: "META", sentiment: 0.61, source: "alpha_vantage", passes_threshold: true },
        { ticker: "AAPL", sentiment: 0.55, source: "finnhub", passes_threshold: true },
        { ticker: "TSLA", sentiment: 0.31, source: "alpha_vantage", passes_threshold: false },
        { ticker: "GME", sentiment: 0.29, source: "finnhub", passes_threshold: false },
      ],
    },
    kelly: {
      status: "ok",
      current_fraction: 0.25,
      edge_threshold: 0.015,
      positions_sized: 3,
      last_calculated: new Date(Date.now() - 15 * 60 * 1000).toISOString(),
      bankroll: 94.42,
      peak_bankroll: 100.0,
      current_drawdown_pct: 0.058,
      halt_triggered: false,
    },
  },
  cascade_summary: {
    total_sources: 5,
    sources_healthy: 4,
    sources_degraded: 1,
    sources_down: 0,
    cascade_order: ["grok_420", "grok_fast", "glm"],
  },
};

const MOCK_HISTORY = [
  { timestamp: new Date(Date.now() - 4 * 3600 * 1000).toISOString(), gdelt_risk: 0.38, stock_sentiment: 0.55, top_ticker: "META", top_signals: [{ ticker: "META", sentiment: 0.55 }, { ticker: "AAPL", sentiment: 0.53 }, { ticker: "NVDA", sentiment: 0.51 }] },
  { timestamp: new Date(Date.now() - 3 * 3600 * 1000).toISOString(), gdelt_risk: 0.41, stock_sentiment: 0.62, top_ticker: "NVDA", top_signals: [{ ticker: "NVDA", sentiment: 0.62 }, { ticker: "META", sentiment: 0.58 }, { ticker: "AAPL", sentiment: 0.50 }] },
  { timestamp: new Date(Date.now() - 2 * 3600 * 1000).toISOString(), gdelt_risk: 0.44, stock_sentiment: 0.59, top_ticker: "NVDA", top_signals: [{ ticker: "NVDA", sentiment: 0.59 }, { ticker: "META", sentiment: 0.56 }, { ticker: "TSLA", sentiment: 0.44 }] },
  { timestamp: new Date(Date.now() - 1 * 3600 * 1000).toISOString(), gdelt_risk: 0.40, stock_sentiment: 0.61, top_ticker: "NVDA", top_signals: [{ ticker: "NVDA", sentiment: 0.61 }, { ticker: "META", sentiment: 0.57 }, { ticker: "AAPL", sentiment: 0.52 }] },
];

// ─── API base ─────────────────────────────────────────────────────────────────
const API_BASE = 'http://192.168.68.64:8510';

// ─── Color helpers ─────────────────────────────────────────────────────────────
const STATUS_COLORS = {
  ok: '#00ff9d',
  stale: '#ffbe0b',
  error: '#ff4757',
  disabled: '#666',
};

const STATUS_LABELS = {
  ok: 'HEALTHY',
  stale: 'STALE',
  error: 'DOWN',
  disabled: 'OFFLINE',
};

function statusColor(s) {
  return STATUS_COLORS[s] || '#888';
}

// ─── Mini sparkline (SVG polyline) ────────────────────────────────────────────
function Sparkline({ data, color = '#00ff9d', width = 80, height = 28 }) {
  if (!data || data.length < 2) return <span style={{ color: '#444', fontSize: 11 }}>—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 0.001;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <defs>
        <linearGradient id={`sg-${color.replace('#','')}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ─── Signal card ───────────────────────────────────────────────────────────────
function SignalCard({ label, status, score, subtitle, sparkData, sparkColor, detail }) {
  const color = statusColor(status);
  const label2 = STATUS_LABELS[status] || status?.toUpperCase();

  return (
    <div style={{
      background: 'rgba(6,10,14,0.9)',
      border: `1px solid ${status === 'ok' ? 'rgba(0,255,157,0.2)' : 'rgba(255,71,87,0.2)'}`,
      borderRadius: 12,
      padding: '16px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      minWidth: 0,
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Status dot */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{
          display: 'inline-block',
          width: 8, height: 8,
          borderRadius: '50%',
          background: color,
          boxShadow: `0 0 6px ${color}`,
          flexShrink: 0,
        }} />
        <span style={{ fontSize: 10, color: '#555', letterSpacing: '0.1em', fontFamily: 'DM Sans, sans-serif' }}>
          {label.toUpperCase()}
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 9, color, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.05em' }}>
          {label2}
        </span>
      </div>

      {/* Main score */}
      <div style={{
        fontSize: score != null ? 28 : 20,
        fontWeight: 700,
        fontFamily: 'JetBrains Mono, monospace',
        color: score != null ? color : '#555',
        lineHeight: 1,
      }}>
        {score != null ? score.toFixed(score < 1 ? 3 : 2) : '—'}
      </div>

      {/* Subtitle */}
      <div style={{ fontSize: 11, color: '#7a8fa6', fontFamily: 'DM Sans, sans-serif' }}>
        {subtitle}
      </div>

      {/* Sparkline */}
      {sparkData && (
        <div style={{ marginTop: 4 }}>
          <Sparkline data={sparkData} color={sparkColor || color} />
        </div>
      )}
    </div>
  );
}

// ─── Stock signal row ─────────────────────────────────────────────────────────
function StockRow({ ticker, sentiment, source, passes, rank }) {
  const color = passes ? '#00ff9d' : '#ff4757';
  const barWidth = Math.round(sentiment * 100);

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      padding: '8px 0',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
    }}>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: '#7a8fa6', width: 36, flexShrink: 0 }}>
        {rank}.
      </span>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 13, fontWeight: 700, color: '#e0e8f0', width: 44, flexShrink: 0 }}>
        {ticker}
      </span>
      {/* Bar */}
      <div style={{ flex: 1, height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 3, overflow: 'hidden', minWidth: 60 }}>
        <div style={{
          width: `${barWidth}%`,
          height: '100%',
          background: passes ? 'linear-gradient(90deg, #00ff9d, #00cc7a)' : 'linear-gradient(90deg, #ff4757, #cc3a47)',
          borderRadius: 3,
          transition: 'width 0.4s ease',
        }} />
      </div>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color, width: 44, textAlign: 'right', flexShrink: 0 }}>
        {sentiment.toFixed(2)}
      </span>
      <span style={{ fontFamily: 'DM Sans, sans-serif', fontSize: 10, color: '#4a5568', width: 80, textAlign: 'right', flexShrink: 0 }}>
        {source}
      </span>
    </div>
  );
}

// ─── GDELT event row ──────────────────────────────────────────────────────────
function EventRow({ event, index }) {
  const tone = event.tone || 0;
  const toneColor = tone < -0.1 ? '#ff4757' : tone > 0.1 ? '#00ff9d' : '#ffbe0b';
  const time = event.seendate ? new Date(event.seendate).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) : null;

  return (
    <div style={{
      padding: '8px 0',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      display: 'flex',
      gap: 10,
      alignItems: 'flex-start',
    }}>
      {time && (
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: '#4a5568', width: 40, flexShrink: 0, marginTop: 2 }}>
          {time}
        </span>
      )}
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 12, color: '#c0ccd8', fontFamily: 'DM Sans, sans-serif', lineHeight: 1.4 }}>
          {event.title?.slice(0, 80)}{event.title?.length > 80 ? '…' : ''}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
          {event.regions?.map(r => (
            <span key={r} style={{ fontSize: 9, color: '#00ff9d', background: 'rgba(0,255,157,0.08)', padding: '1px 6px', borderRadius: 3, fontFamily: 'DM Sans, sans-serif' }}>
              {r.replace('geopolitical_', '')}
            </span>
          ))}
        </div>
      </div>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: toneColor, flexShrink: 0, marginTop: 2 }}>
        {tone > 0 ? '+' : ''}{tone.toFixed(2)}
      </span>
    </div>
  );
}

// ─── Cascade order bar ─────────────────────────────────────────────────────────
function CascadeBar({ order, sources }) {
  return (
    <div style={{
      background: 'rgba(6,10,14,0.9)',
      border: '1px solid rgba(0,255,157,0.15)',
      borderRadius: 10,
      padding: '12px 16px',
    }}>
      <div style={{ fontSize: 10, color: '#555', letterSpacing: '0.1em', marginBottom: 8, fontFamily: 'DM Sans, sans-serif' }}>
        CASCADE ORDER
      </div>
      <div style={{ display: 'flex', gap: 0, alignItems: 'center' }}>
        {order.map((src, i) => {
          const srcData = sources?.[src];
          const color = statusColor(srcData?.status || 'error');
          return (
            <React.Fragment key={src}>
              <div style={{
                padding: '4px 10px',
                background: `${color}18`,
                border: `1px solid ${color}40`,
                borderRadius: 6,
                fontSize: 11,
                fontFamily: 'JetBrains Mono, monospace',
                color,
              }}>
                {src}
              </div>
              {i < order.length - 1 && (
                <span style={{ color: '#333', fontSize: 14, margin: '0 2px' }}>→</span>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function SignalsTab({ initialData, initialHistory }) {
  const [data, setData] = useState(initialData || null);
  const [history, setHistory] = useState(initialHistory || null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);

  const fetchCurrent = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/signals/current`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
      setLastRefresh(new Date());
    } catch (e) {
      console.error('[SignalsTab] fetch current failed:', e);
      // Fall back to mock data so the UI remains functional
      if (!data) setData(MOCK_SIGNALS);
    }
  }, [data]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/signals/history?hours=24`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setHistory(json.history || []);
    } catch (e) {
      console.error('[SignalsTab] fetch history failed:', e);
      if (!history) setHistory(MOCK_HISTORY);
    }
  }, [history]);

  // Initial load
  useEffect(() => {
    if (!initialData) fetchCurrent();
    if (!initialHistory) fetchHistory();
  }, []);

  // Poll every 60 seconds
  useEffect(() => {
    const timer = setInterval(fetchCurrent, 60_000);
    return () => clearInterval(timer);
  }, [fetchCurrent]);

  // Poll history every 5 minutes
  useEffect(() => {
    const timer = setInterval(fetchHistory, 5 * 60_000);
    return () => clearInterval(timer);
  }, [fetchHistory]);

  // Use mock if no real data
  const signals = data || MOCK_SIGNALS;
  const hist = history || MOCK_HISTORY;

  const { sources, cascade_summary } = signals;
  const gdelt = sources?.gdelt;
  const grok = sources?.grok;
  const glm = sources?.glm;
  const stock = sources?.stock_hunter;
  const kelly = sources?.kelly;

  // Build sparkline data from history
  const gdeltSpark = hist?.map(h => h.gdelt_risk).slice(-12);
  const stockSpark = hist?.map(h => h.stock_sentiment).slice(-12);

  const refreshStr = lastRefresh
    ? lastRefresh.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    : signals.timestamp?.slice(11, 19);

  const healthy = cascade_summary?.sources_healthy ?? 0;
  const total = cascade_summary?.total_sources ?? 5;

  return (
    <div style={{
      background: '#060a0e',
      minHeight: '100vh',
      color: '#e0e8f0',
      fontFamily: 'DM Sans, sans-serif',
      padding: '0 0 40px 0',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Scanline overlay */}
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        background: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px)',
        pointerEvents: 'none', zIndex: 100,
      }} />

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 24px 16px',
        borderBottom: '1px solid rgba(0,255,157,0.1)',
      }}>
        <div>
          <div style={{ fontSize: 11, color: '#00ff9d', letterSpacing: '0.15em', fontWeight: 600 }}>
            SIGNAL CASCADE STATUS
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#e0e8f0', marginTop: 4, fontFamily: 'JetBrains Mono, monospace' }}>
            <span style={{ color: healthy === total ? '#00ff9d' : healthy > total * 0.6 ? '#ffbe0b' : '#ff4757' }}>
              ● {healthy}/{total}
            </span>
            <span style={{ color: '#4a5568', fontSize: 14, fontWeight: 400, marginLeft: 12 }}>
              sources healthy
            </span>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <div style={{ fontSize: 11, color: '#4a5568' }}>
            Updated: {refreshStr}
          </div>
          <button
            onClick={() => { fetchCurrent(); fetchHistory(); }}
            style={{
              background: 'rgba(0,255,157,0.08)',
              border: '1px solid rgba(0,255,157,0.25)',
              borderRadius: 6,
              color: '#00ff9d',
              fontSize: 11,
              padding: '5px 12px',
              cursor: 'pointer',
              fontFamily: 'DM Sans, sans-serif',
              display: 'flex',
              alignItems: 'center',
              gap: 5,
            }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Signal cards grid */}
      <div style={{ padding: '20px 24px', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
        <SignalCard
          label="GDELT"
          status={gdelt?.status}
          score={gdelt?.geo_risk_score}
          subtitle={`${gdelt?.event_count ?? 0} events · tone ${((gdelt?.avg_tone ?? 0) * 100).toFixed(0)}%`}
          sparkData={gdeltSpark}
          sparkColor="#ffbe0b"
        />
        <SignalCard
          label="GROK"
          status={grok?.status}
          score={grok?.last_sentiment}
          subtitle={grok?.model ? `${grok.model} · ${grok.last_market || '—'}` : 'no data'}
          sparkData={stockSpark}
          sparkColor="#00ff9d"
        />
        <SignalCard
          label="GLM"
          status={glm?.status}
          score={glm?.status !== 'disabled' ? glm?.last_sentiment : null}
          subtitle={glm?.status === 'disabled' ? 'fallback only' : `${glm?.last_market || '—'}`}
          sparkData={null}
          sparkColor="#8b5cf6"
        />
        <SignalCard
          label="NEWS"
          status={stock?.status}
          score={stock?.top_signals?.[0]?.sentiment}
          subtitle={`${stock?.active_tickers ?? 0} tickers · ${stock?.news_sources_active?.join(', ') || '—'}`}
          sparkData={stockSpark}
          sparkColor="#00ff9d"
        />
      </div>

      {/* Kelly pipeline */}
      <div style={{ padding: '0 24px 16px' }}>
        <div style={{
          background: 'rgba(6,10,14,0.9)',
          border: '1px solid rgba(0,255,157,0.15)',
          borderRadius: 10,
          padding: '14px 18px',
          display: 'flex',
          gap: 24,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}>
          <div>
            <div style={{ fontSize: 10, color: '#555', letterSpacing: '0.1em', marginBottom: 4 }}>KELLY PIPELINE</div>
            <div style={{ display: 'flex', gap: 20 }}>
              {[
                { label: 'Fraction', value: kelly?.current_fraction?.toFixed(3) ?? '—' },
                { label: 'Edge Min', value: kelly?.edge_threshold ? `${(kelly.edge_threshold * 100).toFixed(1)}%` : '—' },
                { label: 'Sized', value: `${kelly?.positions_sized ?? 0} pos` },
                { label: 'Bankroll', value: kelly?.bankroll ? `$${kelly.bankroll.toFixed(2)}` : '—' },
                { label: 'Drawdown', value: kelly?.current_drawdown_pct ? `${(kelly.current_drawdown_pct * 100).toFixed(1)}%` : '—' },
              ].map(({ label, value }) => (
                <div key={label} style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: '#00ff9d' }}>{value}</div>
                  <div style={{ fontSize: 9, color: '#4a5568', letterSpacing: '0.08em', marginTop: 2 }}>{label}</div>
                </div>
              ))}
            </div>
          </div>
          {kelly?.halt_triggered && (
            <div style={{ marginLeft: 'auto', background: 'rgba(255,71,87,0.15)', border: '1px solid rgba(255,71,87,0.3)', borderRadius: 6, padding: '6px 12px', fontSize: 11, color: '#ff4757', fontWeight: 700 }}>
              ⚠ CIRCUIT HALT
            </div>
          )}
        </div>
      </div>

      {/* Cascade order */}
      <div style={{ padding: '0 24px 16px' }}>
        <CascadeBar order={cascade_summary?.cascade_order || []} sources={sources} />
      </div>

      {/* Two-column: GDELT events + Stock signals */}
      <div style={{ padding: '0 24px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* GDELT Top Events */}
        <div style={{
          background: 'rgba(6,10,14,0.9)',
          border: '1px solid rgba(0,255,157,0.12)',
          borderRadius: 10,
          padding: '14px 16px',
        }}>
          <div style={{ fontSize: 10, color: '#555', letterSpacing: '0.1em', marginBottom: 10 }}>
            GDELT TOP EVENTS
          </div>
          <div style={{ maxHeight: 200, overflowY: 'auto' }}>
            {(gdelt?.top_events || []).slice(0, 5).map((evt, i) => (
              <EventRow key={i} event={evt} index={i} />
            ))}
            {(!gdelt?.top_events || gdelt.top_events.length === 0) && (
              <div style={{ color: '#4a5568', fontSize: 12, padding: '12px 0' }}>
                No events — GDELT API may be rate-limited
              </div>
            )}
          </div>
        </div>

        {/* Top Stock Signals */}
        <div style={{
          background: 'rgba(6,10,14,0.9)',
          border: '1px solid rgba(0,255,157,0.12)',
          borderRadius: 10,
          padding: '14px 16px',
        }}>
          <div style={{ fontSize: 10, color: '#555', letterSpacing: '0.1em', marginBottom: 10 }}>
            TOP STOCK SIGNALS
          </div>
          <div>
            {(stock?.top_signals || []).slice(0, 5).map((sig, i) => (
              <StockRow
                key={sig.ticker}
                ticker={sig.ticker}
                sentiment={sig.sentiment}
                source={sig.source}
                passes={sig.passes_threshold}
                rank={i + 1}
              />
            ))}
            {(!stock?.top_signals || stock.top_signals.length === 0) && (
              <div style={{ color: '#4a5568', fontSize: 12, padding: '12px 0' }}>
                No stock signals available
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Sparkline history strip */}
      {hist && hist.length > 1 && (
        <div style={{ padding: '16px 24px 0' }}>
          <div style={{ fontSize: 10, color: '#555', letterSpacing: '0.1em', marginBottom: 10 }}>24H TREND</div>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', overflowX: 'auto', paddingBottom: 4 }}>
            <div style={{ flex: 1, minWidth: 120 }}>
              <div style={{ fontSize: 9, color: '#4a5568', marginBottom: 4 }}>GDELT RISK</div>
              <Sparkline data={gdeltSpark} color="#ffbe0b" width={120} height={32} />
            </div>
            <div style={{ flex: 1, minWidth: 120 }}>
              <div style={{ fontSize: 9, color: '#4a5568', marginBottom: 4 }}>STOCK SENTIMENT</div>
              <Sparkline data={stockSpark} color="#00ff9d" width={120} height={32} />
            </div>
          </div>
        </div>
      )}

      {/* Loading overlay */}
      {loading && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(6,10,14,0.8)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 200,
        }}>
          <div style={{ color: '#00ff9d', fontFamily: 'JetBrains Mono, monospace', fontSize: 14 }}>
            Loading signals…
          </div>
        </div>
      )}
    </div>
  );
}
