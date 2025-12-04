"""Async parallel log processor with worker pool.

This module provides the main async processing engine for parallel log analysis.
Processes multiple logs concurrently while maintaining thread safety and error isolation.
"""

import asyncio
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from agent.utils.logger import log_info, log_error, log_warning, log_debug
from agent.utils.thread_safe import ThreadSafeDeduplicator, ProcessingStats, RateLimiter
from agent.jira.utils import normalize_log_message
from agent.config import get_config


class AsyncLogProcessor:
    """Async log processor with worker pool for parallel processing."""

    def __init__(self, max_workers: int = 5, enable_rate_limiting: bool = True):
        """Initialize async processor.

        Args:
            max_workers: Maximum number of concurrent workers
            enable_rate_limiting: Enable rate limiting for API calls
        """
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)
        self.deduplicator = ThreadSafeDeduplicator()
        self.stats = ProcessingStats()
        self.config = get_config()

        # Rate limiting
        self.rate_limiter = None
        if enable_rate_limiting:
            # Limit to 10 API calls per second to avoid overwhelming services
            self.rate_limiter = RateLimiter(max_calls=10, time_window=1.0)

        log_info(
            "Async processor initialized",
            max_workers=max_workers,
            rate_limiting=enable_rate_limiting
        )

    async def process_logs(self, logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process logs in parallel using worker pool.

        Args:
            logs: List of log entries to process

        Returns:
            Processing summary with results and statistics
        """
        if not logs:
            log_info("No logs to process")
            return {"processed": 0, "results": []}

        await self.stats.set_total_logs(len(logs))
        await self.stats.record_start()

        log_info(
            "Starting async parallel processing",
            total_logs=len(logs),
            workers=self.max_workers
        )

        # Create tasks for all logs
        tasks = [
            self._process_log_with_tracking(i, log)
            for i, log in enumerate(logs)
        ]

        # Process all logs concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        await self.stats.record_end()

        # Separate successful results from errors
        successful_results = []
        errors = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                errors.append({"log_index": i, "error": str(result)})
                await self.stats.record_error()
                log_error(f"Error processing log {i}", error=str(result))
            else:
                successful_results.append(result)

        # Log final statistics
        summary = await self.get_summary()
        log_info(
            "Async processing completed",
            total_logs=len(logs),
            successful=len(successful_results),
            errors=len(errors),
            duration_seconds=summary.get("duration_seconds", 0)
        )

        return {
            "processed": len(logs),
            "successful": len(successful_results),
            "errors": len(errors),
            "results": successful_results,
            "error_details": errors,
            "stats": summary
        }

    async def _process_log_with_tracking(self, index: int, log: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single log with time tracking.

        Args:
            index: Log index in the batch
            log: Log entry data

        Returns:
            Processing result
        """
        start_time = time.time()

        try:
            result = await self._process_single_log(index, log)
            processing_time = time.time() - start_time
            await self.stats.record_log_processed(processing_time)

            # Log progress every 10 logs
            if (index + 1) % 10 == 0:
                await self.stats.log_progress()

            return result

        except Exception as e:
            processing_time = time.time() - start_time
            log_error(
                f"Error processing log {index}",
                error=str(e),
                processing_time=processing_time
            )
            raise

    async def _process_single_log(self, index: int, log: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single log with semaphore control.

        Args:
            index: Log index
            log: Log entry data

        Returns:
            Processing result with action taken
        """
        # Acquire semaphore to limit concurrent processing
        async with self.semaphore:
            log_debug(f"Processing log {index}", logger=log.get("logger"))

            # Apply rate limiting if enabled
            if self.rate_limiter:
                await self.rate_limiter.acquire()

            # Step 1: Check for duplicates
            log_key = self._generate_log_key(log)
            if await self.deduplicator.is_duplicate(log_key):
                await self.stats.record_duplicate()
                return {
                    "index": index,
                    "action": "skipped",
                    "reason": "duplicate",
                    "log_key": log_key
                }

            # Step 2: Analyze log (will be replaced with actual async analysis)
            # For now, this is a placeholder that calls the sync version
            analysis_result = await self._analyze_log_async(log)

            # Step 3: Handle ticket creation based on analysis
            if analysis_result.get("create_ticket"):
                ticket_result = await self._handle_ticket_creation(log, analysis_result)

                if ticket_result["action"] == "created":
                    await self.stats.record_ticket_created()
                elif ticket_result["action"] == "simulated":
                    await self.stats.record_ticket_simulated()

                return {
                    "index": index,
                    **ticket_result,
                    "analysis": analysis_result
                }

            return {
                "index": index,
                "action": "analyzed",
                "create_ticket": False,
                "analysis": analysis_result
            }

    def _generate_log_key(self, log: Dict[str, Any]) -> str:
        """Generate unique key for log deduplication.

        Args:
            log: Log entry

        Returns:
            Unique key string
        """
        raw_msg = log.get('message', '<no message>')
        norm_msg = normalize_log_message(raw_msg)
        logger = log.get('logger', 'unknown')

        return f"{logger}|{norm_msg or raw_msg}"

    async def _analyze_log_async(self, log: Dict[str, Any]) -> Dict[str, Any]:
        """Async wrapper for log analysis.

        Currently calls sync version in executor. Will be replaced with
        fully async version in next iteration.

        Args:
            log: Log entry data

        Returns:
            Analysis result
        """
        # Import here to avoid circular dependency
        from agent.nodes.analysis import analyze_log

        # For now, run sync analysis in executor to not block
        loop = asyncio.get_event_loop()

        state = {
            "log_data": log,
            "log_message": log.get("message", "")
        }

        # Run sync function in thread pool executor
        result = await loop.run_in_executor(None, analyze_log, state)

        return result

    async def _handle_ticket_creation(
        self,
        log: Dict[str, Any],
        analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle ticket creation/duplication check with async Jira searches.

        Args:
            log: Original log data
            analysis: Analysis result

        Returns:
            Ticket creation result
        """
        # Import here to avoid circular dependency
        from agent.jira.async_client import AsyncJiraClient
        from agent.jira.async_match import find_similar_ticket_async, check_fingerprint_duplicate_async

        # Use async Jira client for duplicate detection (the bottleneck)
        async with AsyncJiraClient() as jira_client:
            # Check for duplicates using async matching
            summary = analysis.get("summary", "")
            state = {
                "log_data": log,
                **analysis
            }

            # Check fingerprint cache first
            fingerprint = analysis.get("fingerprint", "")
            if fingerprint:
                is_dup, existing_key = await check_fingerprint_duplicate_async(fingerprint, jira_client)
                if is_dup:
                    await self.stats.record_duplicate()
                    return {
                        "action": "duplicate",
                        "ticket_key": existing_key,
                        "decision": "duplicate_found",
                        "reason": f"Fingerprint duplicate: {existing_key}"
                    }

            # Check similarity (this is the expensive Jira search operation)
            similar_key, score, _ = await find_similar_ticket_async(summary, jira_client, state)
            if similar_key:
                await self.stats.record_duplicate()
                return {
                    "action": "duplicate",
                    "ticket_key": similar_key,
                    "decision": "similar_found",
                    "reason": f"Similar ticket found (score: {score:.2f}): {similar_key}"
                }

        # No duplicate found - use sync ticket creation
        # (Ticket creation is one API call, not the bottleneck)
        from agent.nodes.ticket import create_ticket

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, create_ticket, state)

        return {
            "action": "created" if result.get("ticket_key") else "simulated",
            "ticket_key": result.get("ticket_key"),
            "decision": result.get("decision"),
            "reason": result.get("reason")
        }

    async def get_summary(self) -> Dict[str, Any]:
        """Get processing statistics summary.

        Returns:
            Statistics dictionary
        """
        return await self.stats.get_summary()

    async def get_dedup_stats(self) -> Dict[str, int]:
        """Get deduplication statistics.

        Returns:
            Deduplication stats
        """
        return await self.deduplicator.get_stats()


async def process_logs_parallel(
    logs: List[Dict[str, Any]],
    max_workers: int = 5,
    enable_rate_limiting: bool = True
) -> Dict[str, Any]:
    """Convenience function to process logs in parallel.

    Args:
        logs: List of log entries
        max_workers: Maximum concurrent workers
        enable_rate_limiting: Enable rate limiting

    Returns:
        Processing results and statistics
    """
    processor = AsyncLogProcessor(
        max_workers=max_workers,
        enable_rate_limiting=enable_rate_limiting
    )

    return await processor.process_logs(logs)
