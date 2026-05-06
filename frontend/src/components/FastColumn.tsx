import React from 'react';
import { ParallaxFinding } from '../types/parallax';
import { FindingCard } from './FindingCard';

interface FastColumnProps {
  findings: ParallaxFinding[];
  latency: number | null;
}

export function FastColumn({ findings, latency }: FastColumnProps) {
  return (
    <div className="flex-1 border-r border-gray-800 p-4 bg-[#111118]">
      <div className="mb-4 pb-4 border-b border-gray-800">
        <h2 className="text-xl font-bold text-gray-100 flex items-center gap-2">
          <span className="text-yellow-400">⚡</span> Fast Analysis
        </h2>
        <p className="text-xs text-gray-500 mt-1 uppercase tracking-wider">Qwen2.5-Coder-7B</p>
        <p className="text-xs text-gray-500">Running on AMD MI300X</p>
      </div>

      {latency === null ? (
        <div className="flex flex-col items-center justify-center h-48 space-y-4">
          <div className="w-8 h-8 border-4 border-yellow-500/20 border-t-yellow-500 rounded-full animate-spin"></div>
          <p className="text-gray-500 text-sm animate-pulse">Scanning footprint...</p>
        </div>
      ) : (
        <div>
          {findings.map(f => <FindingCard key={f.id} finding={f} />)}
          <div className="mt-4 text-xs font-mono text-gray-500 border-t border-gray-800 pt-2 text-right">
            {latency.toLocaleString()}ms ──────────────
          </div>
        </div>
      )}
    </div>
  );
}
