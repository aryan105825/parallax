#!/usr/bin/env python3
import concurrent.futures
import time
import requests
import sys

URL = "http://localhost:3000/analyze"
CONCURRENCY = 20
REQUESTS = 100

def make_request():
    start = time.time()
    try:
        res = requests.post(URL, json={"text": "test payload"}, timeout=15)
        status = res.status_code
    except Exception:
        status = 500
    return (time.time() - start) * 1000, status

def run_load_test():
    print(f"Starting load test on {URL} with {CONCURRENCY} concurrent users for {REQUESTS} requests...")
    
    latencies = []
    errors = 0
    start_total = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [executor.submit(make_request) for _ in range(REQUESTS)]
        for future in concurrent.futures.as_completed(futures):
            lat, status = future.result()
            if status == 200:
                latencies.append(lat)
            else:
                errors += 1
                
    total_time = time.time() - start_total
    
    if latencies:
        latencies.sort()
        p95_idx = int(len(latencies) * 0.95)
        p95 = latencies[p95_idx]
        avg = sum(latencies) / len(latencies)
        print("\n--- Benchmark Results ---")
        print(f"Total Time: {total_time:.2f}s")
        print(f"Throughput: {len(latencies)/total_time:.2f} req/s")
        print(f"P95 Latency: {p95:.2f}ms")
        print(f"Average Latency: {avg:.2f}ms")
        print(f"Errors (Includes HTTP 503 from BufferPool Backpressure): {errors}")
        print("\n--- Mathematical Proof of Parallelism ---")
        print("If P95 latency approximates the Slow Model execution time (~5000ms),")
        print("rather than the sum of both models (Slow + Fast), then strict parallel execution is mathematically verified.")
    else:
        print("All requests failed. Is the Rust Engine running?")
        sys.exit(1)

if __name__ == "__main__":
    run_load_test()
