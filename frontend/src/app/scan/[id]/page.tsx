import { DualAnalysisPanel } from "@/components/DualAnalysisPanel";
import Link from "next/link";

export default function ScanPage({ params }: { params: { id: string } }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between items-end max-w-7xl mx-auto w-full">
        <div>
          <Link href="/" className="text-blue-400 hover:underline text-sm mb-2 inline-block">
            ← New Scan
          </Link>
          <h2 className="text-2xl font-bold">Parallel Execution Live Monitor</h2>
          <p className="text-gray-500 font-mono text-sm mt-1">Scan ID: {params.id}</p>
        </div>
      </div>
      
      <DualAnalysisPanel scanId={params.id} />
    </div>
  );
}
