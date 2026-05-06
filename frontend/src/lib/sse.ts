import { useState, useEffect } from 'react';
import { ParallaxStreamState } from '../types/parallax';
import { PIPELINE_URL } from './api';

export function useParallaxStream(scanId: string) {
  const [state, setState] = useState<ParallaxStreamState>({
    events: [],
    fastFindings: [],
    deepFindings: [],
    consensusFindings: [],
    prUrl: null,
    latencies: { fast: null, deep: null, savings: null },
    status: 'pending'
  });

  useEffect(() => {
    if (!scanId) return;
    
    let retryCount = 0;
    const maxRetries = 5;
    let eventSource: EventSource | null = null;
    const abortController = new AbortController();

    const connect = () => {
      eventSource = new EventSource(`${PIPELINE_URL}/scan/${scanId}/stream`);

      eventSource.addEventListener('agent_start', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        setState(prev => ({
          ...prev,
          status: 'running',
          events: [...prev.events, data]
        }));
      });

      eventSource.addEventListener('agent_complete', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        setState(prev => {
          const newState = { ...prev, events: [...prev.events, data] };
          if (data.agent === 'fast_analyst' && data.data) {
             newState.latencies.fast = data.data.latency_ms;
             if (data.data.fast_findings) newState.fastFindings = data.data.fast_findings;
          }
          if (data.agent === 'deep_analyst' && data.data) {
             newState.latencies.deep = data.data.latency_ms;
             if (data.data.deep_findings) newState.deepFindings = data.data.deep_findings;
          }
          if (data.agent === 'consensus' && data.data) {
             if (data.data.consensus_findings) newState.consensusFindings = data.data.consensus_findings;
          }
          if (data.agent === 'pr_agent' && data.data) {
             if (data.data.pr_url) newState.prUrl = data.data.pr_url;
          }
          return newState;
        });
      });

      eventSource.addEventListener('parallel_models_complete', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        setState(prev => ({
          ...prev,
          events: [...prev.events, data],
          latencies: {
            fast: data.data.fast_latency_ms,
            deep: data.data.deep_latency_ms,
            savings: data.data.parallel_savings_ms
          }
        }));
      });

      eventSource.addEventListener('pipeline_complete', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        setState(prev => ({
          ...prev,
          status: 'complete',
          events: [...prev.events, data],
          prUrl: data.data?.pr_url || prev.prUrl
        }));
        eventSource?.close();
      });

      eventSource.onerror = () => {
        eventSource?.close();
        if (retryCount < maxRetries) {
          retryCount++;
          setTimeout(connect, 2000);
        } else {
          setState(prev => ({ ...prev, status: 'error' }));
        }
      };
    };

    connect();

    return () => {
      abortController.abort();
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [scanId]);

  return state;
}
