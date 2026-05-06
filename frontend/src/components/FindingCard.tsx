import React from 'react';
import { ParallaxFinding } from '../types/parallax';
import { SeverityBadge } from './SeverityBadge';

interface FindingCardProps {
  finding: ParallaxFinding;
  showDetails?: boolean;
}

export function FindingCard({ finding, showDetails = false }: FindingCardProps) {
  return (
    <div className="bg-[#1e1e2e] border border-gray-700 rounded-lg p-4 mb-3 shadow-md transition-all hover:border-gray-500">
      <div className="flex justify-between items-start mb-2">
        <div className="flex items-center gap-2">
          <span className="text-gray-400 font-mono text-xs">{finding.id}</span>
          {finding.priority && <SeverityBadge priority={finding.priority} />}
        </div>
        <span className="text-gray-300 text-sm font-semibold">{finding.type}</span>
      </div>
      
      <div className="text-gray-300 text-sm font-mono bg-black/40 px-2 py-1 rounded inline-block mb-2 border border-gray-800">
        {finding.file}:{finding.lines}
      </div>

      <p className="text-gray-400 text-sm mb-3">
        {finding.description}
      </p>

      {showDetails && finding.cvss_reasoning && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <p className="text-xs text-gray-500 mb-1 uppercase tracking-wider">CVSS Reasoning</p>
          <p className="text-gray-400 text-sm">{finding.cvss_reasoning}</p>
          <p className="text-xs text-gray-400 mt-2">Score: <span className="text-blue-400 font-bold">{finding.cvss_preliminary || finding.cvss}</span></p>
        </div>
      )}

      {showDetails && finding.cross_file_context && finding.cross_file_context.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <p className="text-xs text-gray-500 mb-1 uppercase tracking-wider">Cross-File Context</p>
          <ul className="list-disc list-inside text-gray-400 text-xs font-mono">
            {finding.cross_file_context.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </div>
      )}

      {finding.fix_code && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <p className="text-xs text-green-500 mb-1 uppercase tracking-wider">Surgical Fix Applied</p>
          <pre className="text-xs text-gray-300 bg-black/60 p-2 rounded overflow-x-auto border border-green-900/30">
            <code>{finding.fix_code}</code>
          </pre>
          <p className="text-xs text-gray-500 mt-2">{finding.fix_explanation}</p>
        </div>
      )}
    </div>
  );
}
