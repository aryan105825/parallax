import React from 'react';
import { ParallaxFinding } from '../types/parallax';
import { FindingCard } from './FindingCard';
import { PRLinkCard } from './PRLinkCard';

interface ConsensusPanelProps {
  findings: ParallaxFinding[];
  prUrl: string | null;
  status: string;
}

export function ConsensusPanel({ findings, prUrl, status }: ConsensusPanelProps) {
  if (findings.length === 0 && status !== 'complete') {
    return null;
  }

  // Determine merge recommendation color
  const hasP0 = findings.some(f => f.priority === 'P0');
  const hasP1 = findings.some(f => f.priority === 'P1');
  
  let headerClass = "bg-green-900 border-green-500 text-green-100";
  let recText = "✅ PASS";
  
  if (hasP1) {
    headerClass = "bg-amber-900 border-amber-500 text-amber-100";
    recText = "⚠️ REVIEW";
  }
  if (hasP0) {
    headerClass = "bg-red-900 border-red-500 text-red-100";
    recText = "⛔ BLOCK";
  }

  return (
    <div className="border-t-2 border-gray-700 bg-[#0d0d12]">
      <div className={`p-3 border-b ${headerClass} font-bold tracking-wide flex justify-between items-center`}>
        <span>{recText} — {findings.length} Confirmed Finding{findings.length !== 1 ? 's' : ''}</span>
      </div>
      
      <div className="p-4">
        {findings.map(f => (
          <FindingCard key={f.id} finding={f} showDetails={true} />
        ))}
        
        {findings.length > 0 && <PRLinkCard url={prUrl} status={status} />}
      </div>
    </div>
  );
}
