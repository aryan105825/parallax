export const PIPELINE_URL = "http://localhost:8003";

export async function initiateScan(trigger: string, targetBranch: string = "main") {
  const res = await fetch(`${PIPELINE_URL}/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trigger, target_branch: targetBranch }),
  });
  
  if (!res.ok) {
    throw new Error(`Failed to initiate scan: ${res.statusText}`);
  }
  return res.json();
}
