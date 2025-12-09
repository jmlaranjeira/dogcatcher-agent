"""Performance benchmark script for sync vs async processing.

This script runs multiple benchmark tests comparing sequential (sync) and
parallel (async) processing modes with different worker configurations.

Usage:
    python tools/benchmark.py --service <service> --hours <hours> --runs <runs>

Example:
    python tools/benchmark.py --service myapp --hours 24 --runs 3
"""

import subprocess
import time
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any


def run_benchmark(
    mode: str,
    workers: int = None,
    service: str = None,
    hours: int = 24,
    limit: int = None
) -> Dict[str, Any]:
    """Run a single benchmark test.

    Args:
        mode: "sync" or "async"
        workers: Number of workers (async mode only)
        service: Datadog service filter
        hours: Hours to look back
        limit: Limit number of logs

    Returns:
        Dictionary with benchmark results
    """
    cmd = [
        sys.executable,
        "main.py",
        "--dry-run"
    ]

    if service:
        cmd.extend(["--service", service])
    if hours:
        cmd.extend(["--hours", str(hours)])
    if limit:
        cmd.extend(["--limit", str(limit)])

    if mode == "async":
        cmd.append("--async")
        if workers:
            cmd.extend(["--workers", str(workers)])

    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}")

    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        duration = time.time() - start_time

        # Parse output for statistics
        output = result.stdout + result.stderr
        stats = parse_output(output)

        return {
            "mode": mode,
            "workers": workers if mode == "async" else 1,
            "duration_seconds": duration,
            "duration_formatted": format_duration(duration),
            "return_code": result.returncode,
            "success": result.returncode == 0,
            "stats": stats,
            "output_sample": output[:500]  # First 500 chars for debugging
        }

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return {
            "mode": mode,
            "workers": workers if mode == "async" else 1,
            "duration_seconds": duration,
            "duration_formatted": format_duration(duration),
            "return_code": -1,
            "success": False,
            "error": "Timeout (>10 minutes)",
            "stats": {}
        }
    except Exception as e:
        duration = time.time() - start_time
        return {
            "mode": mode,
            "workers": workers if mode == "async" else 1,
            "duration_seconds": duration,
            "duration_formatted": format_duration(duration),
            "return_code": -1,
            "success": False,
            "error": str(e),
            "stats": {}
        }


def parse_output(output: str) -> Dict[str, Any]:
    """Parse benchmark output for statistics.

    Args:
        output: Combined stdout/stderr from benchmark run

    Returns:
        Dictionary with parsed statistics
    """
    stats = {
        "logs_processed": 0,
        "logs_successful": 0,
        "logs_errors": 0,
        "tickets_created": 0,
        "duplicates_found": 0
    }

    lines = output.split('\n')

    for line in lines:
        # Parse async processing completed message
        if "Async processing completed" in line:
            if "processed=" in line:
                try:
                    stats["logs_processed"] = int(line.split("processed=")[1].split()[0].rstrip(','))
                except:
                    pass
            if "successful=" in line:
                try:
                    stats["logs_successful"] = int(line.split("successful=")[1].split()[0].rstrip(','))
                except:
                    pass
            if "errors=" in line:
                try:
                    stats["logs_errors"] = int(line.split("errors=")[1].split()[0].rstrip(','))
                except:
                    pass

        # Parse logs loaded message
        if "Logs loaded" in line and "log_count=" in line:
            try:
                stats["logs_processed"] = int(line.split("log_count=")[1].split()[0].rstrip(','))
            except:
                pass

        # Parse ticket creation
        if "Jira ticket created" in line or "would create Jira ticket" in line:
            stats["tickets_created"] += 1

        # Parse duplicates
        if "duplicate found" in line.lower():
            stats["duplicates_found"] += 1

    return stats


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "2m 30s")
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def calculate_improvement(sync_duration: float, async_duration: float) -> Dict[str, Any]:
    """Calculate performance improvement metrics.

    Args:
        sync_duration: Duration in sync mode (seconds)
        async_duration: Duration in async mode (seconds)

    Returns:
        Dictionary with improvement metrics
    """
    if sync_duration == 0:
        return {"speedup": 0, "time_saved": 0, "percentage": 0}

    speedup = sync_duration / async_duration if async_duration > 0 else 0
    time_saved = sync_duration - async_duration
    percentage = (time_saved / sync_duration * 100) if sync_duration > 0 else 0

    return {
        "speedup": round(speedup, 2),
        "time_saved_seconds": round(time_saved, 2),
        "time_saved_formatted": format_duration(time_saved),
        "percentage": round(percentage, 1)
    }


def print_results(results: List[Dict[str, Any]]):
    """Print benchmark results in a formatted table.

    Args:
        results: List of benchmark result dictionaries
    """
    print("\n" + "="*80)
    print("BENCHMARK RESULTS")
    print("="*80)

    # Print individual results
    print(f"\n{'Mode':<10} {'Workers':<8} {'Duration':<12} {'Logs':<8} {'Success':<10} {'Status'}")
    print("-" * 80)

    for r in results:
        mode = r['mode'].upper()
        workers = r['workers']
        duration = r['duration_formatted']
        logs = r['stats'].get('logs_processed', 'N/A')
        success = '✓' if r['success'] else '✗'
        status = 'OK' if r['success'] else r.get('error', 'FAILED')

        print(f"{mode:<10} {workers:<8} {duration:<12} {logs:<8} {success:<10} {status}")

    # Calculate and print comparisons
    sync_results = [r for r in results if r['mode'] == 'sync' and r['success']]
    async_results = [r for r in results if r['mode'] == 'async' and r['success']]

    if sync_results and async_results:
        print("\n" + "="*80)
        print("PERFORMANCE COMPARISON")
        print("="*80)

        sync_baseline = sync_results[0]['duration_seconds']

        print(f"\n{'Workers':<10} {'Duration':<15} {'Speedup':<10} {'Time Saved':<15} {'Improvement'}")
        print("-" * 80)

        # Sync baseline
        print(f"{'1 (sync)':<10} {sync_results[0]['duration_formatted']:<15} {'1.0x':<10} {'-':<15} {'baseline'}")

        # Async results
        for r in async_results:
            improvement = calculate_improvement(sync_baseline, r['duration_seconds'])
            print(
                f"{r['workers']:<10} "
                f"{r['duration_formatted']:<15} "
                f"{improvement['speedup']}x{'':<7} "
                f"{improvement['time_saved_formatted']:<15} "
                f"{improvement['percentage']}%"
            )

        # Best performer
        if async_results:
            best = min(async_results, key=lambda x: x['duration_seconds'])
            improvement = calculate_improvement(sync_baseline, best['duration_seconds'])

            print("\n" + "="*80)
            print("BEST CONFIGURATION")
            print("="*80)
            print(f"Mode: ASYNC with {best['workers']} workers")
            print(f"Duration: {best['duration_formatted']} (vs {sync_results[0]['duration_formatted']} sync)")
            print(f"Speedup: {improvement['speedup']}x faster")
            print(f"Time saved: {improvement['time_saved_formatted']} ({improvement['percentage']}% improvement)")


def save_results(results: List[Dict[str, Any]], output_file: str = None):
    """Save benchmark results to JSON file.

    Args:
        results: List of benchmark result dictionaries
        output_file: Output file path (optional)
    """
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"benchmark_results_{timestamp}.json"

    output_path = Path("tools") / output_file

    benchmark_data = {
        "timestamp": datetime.now().isoformat(),
        "results": results
    }

    with open(output_path, 'w') as f:
        json.dump(benchmark_data, f, indent=2)

    print(f"\n✓ Results saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Run performance benchmarks for sync vs async processing"
    )
    parser.add_argument(
        "--service",
        type=str,
        help="Datadog service filter"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Number of hours to look back (default: 24)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of logs to process"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per configuration (default: 1)"
    )
    parser.add_argument(
        "--workers",
        type=str,
        default="3,5,10",
        help="Comma-separated list of worker counts for async tests (default: 3,5,10)"
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip sync baseline test"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file name for results (default: benchmark_results_<timestamp>.json)"
    )

    args = parser.parse_args()

    # Parse worker counts
    worker_counts = [int(w.strip()) for w in args.workers.split(',')]

    print("="*80)
    print("DOGCATCHER AGENT - PERFORMANCE BENCHMARK")
    print("="*80)
    print(f"Service: {args.service or 'ALL'}")
    print(f"Hours back: {args.hours}")
    print(f"Limit: {args.limit or 'None'}")
    print(f"Runs per config: {args.runs}")
    print(f"Worker counts: {worker_counts}")
    print(f"Skip sync: {args.skip_sync}")
    print("="*80)

    all_results = []

    # Run sync baseline
    if not args.skip_sync:
        print("\n🔄 Running SYNC baseline...")
        for run in range(args.runs):
            if args.runs > 1:
                print(f"\n  Run {run + 1}/{args.runs}")

            result = run_benchmark(
                mode="sync",
                service=args.service,
                hours=args.hours,
                limit=args.limit
            )
            result["run"] = run + 1
            all_results.append(result)

            if result['success']:
                print(f"  ✓ Completed in {result['duration_formatted']}")
            else:
                print(f"  ✗ Failed: {result.get('error', 'Unknown error')}")

    # Run async tests with different worker counts
    for workers in worker_counts:
        print(f"\n🔄 Running ASYNC with {workers} workers...")
        for run in range(args.runs):
            if args.runs > 1:
                print(f"\n  Run {run + 1}/{args.runs}")

            result = run_benchmark(
                mode="async",
                workers=workers,
                service=args.service,
                hours=args.hours,
                limit=args.limit
            )
            result["run"] = run + 1
            all_results.append(result)

            if result['success']:
                print(f"  ✓ Completed in {result['duration_formatted']}")
            else:
                print(f"  ✗ Failed: {result.get('error', 'Unknown error')}")

    # Print summary
    print_results(all_results)

    # Save results
    save_results(all_results, args.output)

    print("\n✓ Benchmark complete!")


if __name__ == "__main__":
    main()
