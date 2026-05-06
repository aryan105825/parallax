'use client';

import React, { useState } from 'react';
import { initiateScan } from '../lib/api';
import { useRouter } from 'next/navigation';

export function CodeInput() {
  const [trigger, setTrigger] = useState('');
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleScan = async () => {
    if (!trigger.trim()) return;
    setLoading(true);
    try {
      const data = await initiateScan(trigger);
      if (data.scan_id) {
        router.push(`/scan/${data.scan_id}`);
      }
    } catch (e) {
      console.error(e);
      setLoading(false);
    }
  };

  return (
    <div className="w-full max-w-3xl mx-auto mt-12 bg-[#1e1e2e] p-6 rounded-lg border border-gray-700 shadow-xl">
      <h2 className="text-xl font-semibold text-gray-200 mb-4 flex items-center gap-2">
        <span className="text-blue-500">🔍</span> Start Security Scan
      </h2>
      <textarea
        className="w-full h-32 bg-black border border-gray-700 rounded p-3 text-sm font-mono text-gray-300 focus:outline-none focus:border-blue-500 transition-colors"
        placeholder="Paste raw code snippet or provide a GitHub URL (e.g. https://github.com/digininja/DVWA@main)"
        value={trigger}
        onChange={(e) => setTrigger(e.target.value)}
      />
      <div className="mt-4 flex justify-end">
        <button
          onClick={handleScan}
          disabled={loading || !trigger.trim()}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded shadow-lg transition-colors"
        >
          {loading ? 'Initializing...' : 'Run Parallel Scan on AMD MI300X'}
        </button>
      </div>
    </div>
  );
}
