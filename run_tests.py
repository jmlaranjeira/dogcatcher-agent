#!/usr/bin/env python3
"""Test runner script for dogcatcher-agent."""

import sys
import subprocess
from pathlib import Path


def main():
    """Run tests with pytest."""
    project_root = Path(__file__).parent

    # Check if pytest is available
    try:
        import pytest
    except ImportError:
        print("❌ pytest is not installed. Please install it with:")
        print("   pip install pytest")
        sys.exit(1)

    # Run tests
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(project_root / "tests"),
        "-v",
        "--tb=short",
        "--color=yes",
    ]

    # Add specific test markers if requested
    if len(sys.argv) > 1:
        if sys.argv[1] == "unit":
            cmd.extend(["-m", "unit"])
        elif sys.argv[1] == "config":
            cmd.extend(["-m", "config"])
        elif sys.argv[1] == "ticket":
            cmd.extend(["-m", "ticket"])
        elif sys.argv[1] == "normalization":
            cmd.extend(["-m", "normalization"])
        elif sys.argv[1] == "integration":
            cmd.extend(["-m", "integration"])

    print(f"🧪 Running tests: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, cwd=project_root)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\n⏹️  Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error running tests: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
