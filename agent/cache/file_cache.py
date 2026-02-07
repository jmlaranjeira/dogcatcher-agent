"""File-based persistent cache backend."""

import asyncio
import json
import pickle
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict
import hashlib

from .base import CacheBackend, CacheEntry, CacheStats


class FileCacheBackend(CacheBackend):
    """File-based cache backend with persistence."""

    def __init__(self, cache_dir: str = ".agent_cache/persistent", name: str = "file"):
        super().__init__(name)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / "metadata.json"
        self._lock = asyncio.Lock()

        # Load metadata on initialization
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Load cache metadata from file."""
        try:
            if self.metadata_file.exists():
                with open(self.metadata_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_metadata(self) -> None:
        """Save cache metadata to file."""
        try:
            with open(self.metadata_file, "w") as f:
                json.dump(self.metadata, f, indent=2, default=str)
        except Exception:
            pass

    def _get_file_path(self, key: str) -> Path:
        """Get file path for cache key."""
        # Hash the key to avoid filesystem issues
        key_hash = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
        return self.cache_dir / f"cache_{key_hash}.pkl"

    def _is_expired(self, key: str) -> bool:
        """Check if cache entry is expired."""
        if key not in self.metadata:
            return True

        metadata = self.metadata[key]
        timestamp = datetime.fromisoformat(metadata.get("timestamp", "1970-01-01"))
        ttl = metadata.get("ttl_seconds", 3600)

        if ttl <= 0:  # Never expires
            return False

        return (datetime.now() - timestamp).total_seconds() > ttl

    async def get(self, key: str) -> Optional[Any]:
        """Get value from file cache."""
        async with self._lock:
            try:
                if self._is_expired(key):
                    # Clean up expired entry
                    await self._cleanup_key(key)
                    self._record_miss()
                    return None

                file_path = self._get_file_path(key)
                if not file_path.exists():
                    self._record_miss()
                    return None

                # Load data from file
                with open(file_path, "rb") as f:
                    data = pickle.load(f)  # nosec B301

                # Update access count in metadata
                if key in self.metadata:
                    self.metadata[key]["access_count"] = (
                        self.metadata[key].get("access_count", 0) + 1
                    )
                    self._save_metadata()

                self._record_hit()
                return data

            except Exception:
                self._record_error()
                return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in file cache."""
        async with self._lock:
            try:
                file_path = self._get_file_path(key)

                # Save data to file
                with open(file_path, "wb") as f:
                    pickle.dump(value, f)

                # Update metadata
                self.metadata[key] = {
                    "timestamp": datetime.now().isoformat(),
                    "ttl_seconds": ttl,
                    "access_count": 0,
                    "file_path": str(file_path),
                    "size_bytes": file_path.stat().st_size,
                }

                self._save_metadata()
                return True

            except Exception:
                self._record_error()
                return False

    async def delete(self, key: str) -> bool:
        """Delete key from file cache."""
        async with self._lock:
            try:
                await self._cleanup_key(key)
                return True

            except Exception:
                self._record_error()
                return False

    async def _cleanup_key(self, key: str) -> None:
        """Remove key and its associated file."""
        try:
            # Remove file
            file_path = self._get_file_path(key)
            if file_path.exists():
                file_path.unlink()

            # Remove from metadata
            if key in self.metadata:
                del self.metadata[key]
                self._save_metadata()

        except Exception:
            pass

    async def clear(self) -> bool:
        """Clear all cache entries."""
        async with self._lock:
            try:
                # Remove all cache files
                for file_path in self.cache_dir.glob("cache_*.pkl"):
                    try:
                        file_path.unlink()
                    except Exception:
                        pass

                # Clear metadata
                self.metadata.clear()
                self._save_metadata()

                return True

            except Exception:
                self._record_error()
                return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        async with self._lock:
            try:
                if self._is_expired(key):
                    await self._cleanup_key(key)
                    return False

                file_path = self._get_file_path(key)
                return file_path.exists() and key in self.metadata

            except Exception:
                self._record_error()
                return False

    async def cleanup_expired(self) -> int:
        """Remove expired entries."""
        async with self._lock:
            try:
                expired_keys = []

                for key in list(self.metadata.keys()):
                    if self._is_expired(key):
                        expired_keys.append(key)

                for key in expired_keys:
                    await self._cleanup_key(key)

                return len(expired_keys)

            except Exception:
                self._record_error()
                return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get file cache statistics."""
        try:
            stats = CacheStats()
            stats.hits = self.hits
            stats.misses = self.misses
            stats.errors = self.errors
            stats.size = len(self.metadata)

            if self.metadata:
                # Calculate total disk usage
                stats.memory_usage = sum(
                    entry.get("size_bytes", 0) for entry in self.metadata.values()
                )

                # Find oldest and newest entries
                timestamps = [
                    datetime.fromisoformat(entry["timestamp"])
                    for entry in self.metadata.values()
                    if "timestamp" in entry
                ]

                if timestamps:
                    stats.oldest_entry = min(timestamps)
                    stats.newest_entry = max(timestamps)

            return {
                **stats.to_dict(),
                "backend": self.name,
                "cache_dir": str(self.cache_dir),
                "disk_usage_bytes": stats.memory_usage,
                "metadata_entries": len(self.metadata),
            }

        except Exception:
            return {
                "backend": self.name,
                "error": "Failed to collect stats",
                "hits": self.hits,
                "misses": self.misses,
                "errors": self.errors,
            }

    async def close(self) -> None:
        """Close file cache."""
        async with self._lock:
            self._save_metadata()

    # Additional file-specific methods

    async def cleanup_by_size(self, max_size_mb: int) -> int:
        """Remove oldest entries to stay under size limit."""
        async with self._lock:
            try:
                current_size = sum(
                    entry.get("size_bytes", 0) for entry in self.metadata.values()
                )

                if current_size <= max_size_mb * 1024 * 1024:
                    return 0

                # Sort by timestamp (oldest first)
                sorted_keys = sorted(
                    self.metadata.keys(),
                    key=lambda k: self.metadata[k].get("timestamp", ""),
                )

                removed_count = 0
                for key in sorted_keys:
                    await self._cleanup_key(key)
                    removed_count += 1

                    # Check if we're under the limit
                    current_size = sum(
                        entry.get("size_bytes", 0) for entry in self.metadata.values()
                    )

                    if current_size <= max_size_mb * 1024 * 1024:
                        break

                return removed_count

            except Exception:
                self._record_error()
                return 0

    def get_disk_usage(self) -> int:
        """Get total disk usage in bytes."""
        try:
            return sum(entry.get("size_bytes", 0) for entry in self.metadata.values())
        except Exception:
            return 0
