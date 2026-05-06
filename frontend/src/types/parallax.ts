export interface ParallaxFinding {
  id: string;
  file: string;
  lines: string;
  type: string;
  description: string;
  snippet: string;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  cross_file_context?: string[] | null;
  cvss_reasoning?: string;
  cvss_preliminary?: number;
  cvss?: number;
  owasp?: string;
  cwe?: string;
  priority?: "P0" | "P1" | "P2" | "P3";
  fix_hint?: string;
  source?: "consensus" | "deep_only" | "fast_only" | "conflict_resolved";
  fix_code?: string;
  fix_explanation?: string;
}

export interface ParallaxEvent {
  event: string;
  agent: string;
  timestamp: string;
  data: unknown;
}

export interface ParallaxStreamState {
  events: ParallaxEvent[];
  fastFindings: ParallaxFinding[];
  deepFindings: ParallaxFinding[];
  consensusFindings: ParallaxFinding[];
  prUrl: string | null;
  latencies: {
    fast: number | null;
    deep: number | null;
    savings: number | null;
  };
  status: "pending" | "running" | "complete" | "error";
}
