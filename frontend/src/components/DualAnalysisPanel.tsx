'use client';

import React from 'react';
import { useParallaxStream } from '../lib/sse';
import { FastColumn } from './FastColumn';
import { DeepColumn } from './DeepColumn';
import { ConsensusPanel } from './ConsensusPanel';
import { BenchmarkBar } from './BenchmarkBar';

export function DualAnalysisPanel({ scanId }: { scanId: string }) {
  const state = useParallaxStream(scanId);

  return (
    <div className="w-full max-w-7xl mx-auto border border-gray-700 rounded-lg overflow-hidden flex flex-col bg-black text-gray-200">
      <div className="flex flex-col md:flex-row min-h-[600px]">
        <FastColumn findings={state.fastFindings} latency={state.latencies.fast} />
        <DeepColumn findings={state.deepFindings} latency={state.latencies.deep} />
      </div>
      
      {state.latencies.savings !== null && (
        <BenchmarkBar latencies={state.latencies} />
      )}
      
      {state.latencies.savings !== null && (
        <ConsensusPanel 
          findings={state.consensusFindings} 
          prUrl={state.prUrl} 
          status={state.status} 
        />
      )}
    </div>
  );
}
