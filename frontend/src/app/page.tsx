import { CodeInput } from "@/components/CodeInput";

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center pt-10">
      <div className="text-center max-w-2xl mb-8">
        <h2 className="text-4xl font-extrabold mb-4">Parallel Code Analysis</h2>
        <p className="text-gray-400">
          Leveraging the massive 192GB VRAM of the AMD Instinct™ MI300X to run two distinct security models simultaneously. Fast scanning and deep architectural auditing occur in parallel, completely redefining the speed of security reviews.
        </p>
      </div>
      
      <CodeInput />
    </div>
  );
}
