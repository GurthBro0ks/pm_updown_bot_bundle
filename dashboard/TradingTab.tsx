// TradingTab.tsx — Reference component for slimyai.xyz/mission-control
// Matches retro-cyberpunk theme (purple/indigo gradients, neon accents)
// API: http://NUC_IP:8510/api/trading/status

import React, { useState, useEffect } from 'react';

interface Position {
  ticker: string;
  entry_price: number;
  size: number;
  entry_time?: string;
  sentiment?: number;
  venue?: string;
}

interface Bankroll {
  cash: number;
  positions_value: number;
  total: number;
  pnl: number;
  pnl_pct: number;
}

interface TradingData {
  timestamp: string;
  bankroll: Bankroll;
  positions: Position[];
  phases: Record<string, boolean>;
  recent_trades: any[];
  mode: string;
}

interface HealthData {
  status: string;
  checks: Record<string, { exists: boolean; age_hours: number | null; fresh: boolean }>;
  timestamp: string;
}

interface AirdropTarget {
  name: string;
  tier: string;
  tge: string;
  status: string;
}

interface AirdropData {
  state: any;
  targets: AirdropTarget[];
  timestamp: string;
}

// API base URL - update to your NUC's local IP
const API_BASE = 'http://192.168.1.100:8510';

export default function TradingTab() {
  const [data, setData] = useState<TradingData | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [airdrops, setAirdrops] = useState<AirdropData | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'overview' | 'airdrops'>('overview');

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statusResp, healthResp, airdropResp] = await Promise.all([
          fetch(`${API_BASE}/api/trading/status`),
          fetch(`${API_BASE}/api/trading/health`),
          fetch(`${API_BASE}/api/trading/airdrops`),
        ]);

        if (statusResp.ok) setData(await statusResp.json());
        if (healthResp.ok) setHealth(await healthResp.json());
        if (airdropResp.ok) setAirdrops(await airdropResp.json());
      } catch (e) {
        console.error('Dashboard fetch failed:', e);
      }
      setLoading(false);
    };

    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-cyan-400 animate-pulse">Loading trading data...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-400">Failed to connect to trading bot API</div>
      </div>
    );
  }

  const formatCurrency = (val: number) => `$${val.toFixed(2)}`;
  const formatPnl = (val: number) => (val >= 0 ? `+${val.toFixed(2)}` : val.toFixed(2));

  return (
    <div className="space-y-4">
      {/* Tab Selector */}
      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setActiveTab('overview')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
            activeTab === 'overview'
              ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/30'
              : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
          }`}
        >
          Overview
        </button>
        <button
          onClick={() => setActiveTab('airdrops')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
            activeTab === 'airdrops'
              ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/30'
              : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
          }`}
        >
          Airdrops
        </button>
      </div>

      {activeTab === 'overview' ? (
        <>
          {/* Bankroll Card */}
          <div className="bg-gray-900/80 border border-purple-500/30 rounded-lg p-4 backdrop-blur-sm">
            <h3 className="text-purple-400 text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
              <span className="w-2 h-2 bg-purple-400 rounded-full animate-pulse" />
              Bankroll
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-2xl font-bold text-cyan-400">{formatCurrency(data.bankroll.total)}</div>
                <div className="text-xs text-gray-500">Total Value</div>
              </div>
              <div>
                <div className="text-lg text-green-400">{formatCurrency(data.bankroll.cash)}</div>
                <div className="text-xs text-gray-500">Cash</div>
              </div>
              <div>
                <div className="text-lg text-yellow-400">{formatCurrency(data.bankroll.positions_value)}</div>
                <div className="text-xs text-gray-500">Deployed</div>
              </div>
              <div>
                <div className={`text-lg font-bold ${data.bankroll.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {formatPnl(data.bankroll.pnl)} ({data.bankroll.pnl_pct}%)
                </div>
                <div className="text-xs text-gray-500">P&L</div>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2 text-xs text-gray-600">
              <span className={`px-2 py-0.5 rounded ${data.mode === 'paper' ? 'bg-blue-900/50 text-blue-400' : 'bg-green-900/50 text-green-400'}`}>
                {data.mode.toUpperCase()}
              </span>
              <span>Last updated: {new Date(data.timestamp).toLocaleTimeString()}</span>
            </div>
          </div>

          {/* Phase Status */}
          <div className="bg-gray-900/80 border border-purple-500/30 rounded-lg p-4 backdrop-blur-sm">
            <h3 className="text-purple-400 text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
              <span className="w-2 h-2 bg-purple-400 rounded-full" />
              Phase Status
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {Object.entries(data.phases).map(([phase, active]) => (
                <div
                  key={phase}
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg border ${
                    active
                      ? 'bg-green-900/20 border-green-500/30'
                      : 'bg-red-900/20 border-red-500/30'
                  }`}
                >
                  <div className={`w-2 h-2 rounded-full ${active ? 'bg-green-400 shadow-lg shadow-green-400/50' : 'bg-red-400'}`} />
                  <span className="text-sm text-gray-300">{phase.replace('_', ' ')}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Positions */}
          <div className="bg-gray-900/80 border border-purple-500/30 rounded-lg p-4 backdrop-blur-sm">
            <h3 className="text-purple-400 text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
              <span className="w-2 h-2 bg-cyan-400 rounded-full" />
              Positions ({data.positions.length})
            </h3>
            {data.positions.length === 0 ? (
              <div className="text-gray-500 text-sm py-4 text-center">No open positions</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-gray-500 border-b border-gray-700">
                      <th className="text-left py-2 px-2">Ticker</th>
                      <th className="text-right py-2 px-2">Entry</th>
                      <th className="text-right py-2 px-2">Size</th>
                      <th className="text-right py-2 px-2">Sentiment</th>
                      <th className="text-right py-2 px-2">Venue</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.positions.map((p, i) => (
                      <tr key={i} className="border-b border-gray-800 hover:bg-gray-800/50">
                        <td className="py-2 px-2 text-cyan-300 font-mono font-bold">{p.ticker}</td>
                        <td className="py-2 px-2 text-right text-gray-400">${p.entry_price?.toFixed(2)}</td>
                        <td className="py-2 px-2 text-right text-yellow-400">${p.size?.toFixed(2)}</td>
                        <td className="py-2 px-2 text-right text-purple-400">{p.sentiment?.toFixed(2) || '—'}</td>
                        <td className="py-2 px-2 text-right text-gray-500">{p.venue || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Recent Trades */}
          {data.recent_trades && data.recent_trades.length > 0 && (
            <div className="bg-gray-900/80 border border-purple-500/30 rounded-lg p-4 backdrop-blur-sm">
              <h3 className="text-purple-400 text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
                <span className="w-2 h-2 bg-yellow-400 rounded-full" />
                Recent Trades
              </h3>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {data.recent_trades.slice(-10).reverse().map((trade: any, i: number) => (
                  <div key={i} className="flex justify-between text-sm py-1 px-2 rounded hover:bg-gray-800/50">
                    <span className="text-cyan-300 font-mono">{trade.ticker || trade.symbol}</span>
                    <span className={`${trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.pnl !== undefined ? formatPnl(trade.pnl) : trade.action}
                    </span>
                    <span className="text-gray-500 text-xs">
                      {trade.time ? new Date(trade.time).toLocaleDateString() : ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* API Health */}
          {health && (
            <div className="bg-gray-900/80 border border-purple-500/30 rounded-lg p-4 backdrop-blur-sm">
              <h3 className="text-purple-400 text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${health.status === 'healthy' ? 'bg-green-400' : 'bg-yellow-400'}`} />
                API Health
              </h3>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {Object.entries(health.checks).map(([name, check]: [string, any]) => (
                  <div key={name} className="flex items-center gap-2">
                    <div className={`w-1.5 h-1.5 rounded-full ${check.fresh ? 'bg-green-400' : 'bg-red-400'}`} />
                    <span className="text-gray-400">{name}:</span>
                    <span className={check.fresh ? 'text-green-400' : 'text-red-400'}>
                      {check.exists ? (check.age_hours !== null ? `${check.age_hours}h ago` : 'OK') : 'missing'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        /* Airdrops Tab */
        <>
          <div className="bg-gray-900/80 border border-purple-500/30 rounded-lg p-4 backdrop-blur-sm">
            <h3 className="text-purple-400 text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
              <span className="w-2 h-2 bg-pink-400 rounded-full animate-pulse" />
              Airdrop Targets
            </h3>
            {airdrops?.targets && (
              <div className="space-y-2">
                {airdrops.targets.map((target, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between px-3 py-2 rounded-lg bg-gray-800/50 border border-gray-700"
                  >
                    <div className="flex items-center gap-3">
                      <span className={`px-2 py-0.5 text-xs rounded ${
                        target.tier === 'S' ? 'bg-yellow-900/50 text-yellow-400' : 'bg-gray-700 text-gray-400'
                      }`}>
                        {target.tier}
                      </span>
                      <span className="text-cyan-300 font-mono">{target.name}</span>
                    </div>
                    <div className="flex items-center gap-4 text-xs">
                      <span className="text-gray-500">{target.tge}</span>
                      <span className={`px-2 py-0.5 rounded ${
                        target.status === 'active' ? 'bg-green-900/50 text-green-400' :
                        target.status === 'farming' ? 'bg-blue-900/50 text-blue-400' :
                        target.status === 'pending' ? 'bg-gray-700 text-gray-400' :
                        'bg-pink-900/50 text-pink-400'
                      }`}>
                        {target.status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
