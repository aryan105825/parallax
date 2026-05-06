import React from 'react';

interface PRLinkCardProps {
  url: string | null;
  status: string;
}

export function PRLinkCard({ url, status }: PRLinkCardProps) {
  if (status !== 'complete' && !url) {
    return (
      <div className="flex items-center gap-3 p-3 bg-gray-900 border border-gray-700 rounded text-sm text-gray-400 mt-2">
        <div className="w-4 h-4 border-2 border-gray-600 border-t-gray-400 rounded-full animate-spin"></div>
        Generating surgical fixes and preparing Pull Request...
      </div>
    );
  }

  if (url) {
    return (
      <div className="p-3 bg-green-900/20 border border-green-800 rounded mt-2">
        <p className="text-sm text-green-400 flex items-center gap-2">
          <span>✅</span> Pull Request Opened:
          <a href={url} target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">
            {url}
          </a>
        </p>
      </div>
    );
  }

  return null;
}
