import React from 'react';

interface SeverityBadgeProps {
  priority: string; // P0, P1, P2, P3
}

export function SeverityBadge({ priority }: SeverityBadgeProps) {
  let bgColor = "bg-gray-700 text-gray-200"; // P3 default
  if (priority === "P0") bgColor = "bg-red-900 text-red-100 font-bold border border-red-500";
  if (priority === "P1") bgColor = "bg-amber-700 text-amber-100 border border-amber-500";
  if (priority === "P2") bgColor = "bg-blue-800 text-blue-100 border border-blue-500";

  return (
    <span className={`px-2 py-1 rounded text-xs tracking-wider ${bgColor}`}>
      {priority}
    </span>
  );
}
