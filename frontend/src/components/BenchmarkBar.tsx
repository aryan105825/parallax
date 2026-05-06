import React from 'react';

interface BenchmarkBarProps {
  latencies: {
    fast: number | null;
    deep: number | null;
    savings: number | null;
  };
}

export function BenchmarkBar({ latencies }: BenchmarkBarProps) {
  if (latencies.fast === null || latencies.deep === null || latencies.savings === null) {
    return null;
  }

  const sequential = latencies.fast + latencies.deep;
  const wallTime = Math.max(latencies.fast, latencies.deep);

  return (
    <div className="bg-[#1e1e2e] border-t border-gray-800 p-4 font-mono text-xs">
      <div className="flex flex-col space-y-2">
        <div className="flex items-center">
          <span className="w-24 text-gray-400">Fast (7B):</span>
          <div className="h-3 bg-yellow-500 rounded" style={{ width: `${(latencies.fast / sequential) * 100}%`, minWidth: '2px' }}></div>
          <span className="ml-3 text-yellow-400">{latencies.fast}ms</span>
        </div>
        
        <div className="flex items-center">
          <span className="w-24 text-gray-400">Deep (32B):</span>
          <div className="h-3 bg-blue-500 rounded" style={{ width: `${(latencies.deep / sequential) * 100}%`, minWidth: '2px' }}></div>
          <span className="ml-3 text-blue-400">{latencies.deep}ms</span>
        </div>

        <div className="flex items-center mt-2 pt-2 border-t border-gray-700">
          <span className="w-24 text-gray-300">Wall time:</span>
          <div className="h-3 bg-white rounded" style={{ width: `${(wallTime / sequential) * 100}%` }}></div>
          <span className="ml-3 text-white font-bold">{wallTime}ms</span>
        </div>

        <div className="flex items-center opacity-50">
          <span className="w-24 text-gray-500">Sequential:</span>
          <div className="h-3 bg-gray-600 rounded w-full"></div>
          <span className="ml-3 text-gray-400">{sequential}ms</span>
        </div>

        <div className="mt-2 text-green-400 font-bold bg-green-900/20 p-2 rounded border border-green-800 text-center">
          AMD MI300X Parallel Savings: {latencies.savings}ms faster
        </div>
      </div>
    </div>
  );
}
