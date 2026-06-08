#!/usr/bin/env python3
"""Kill crawler processes that are using invalid KB IDs."""
import subprocess, re

# Valid KB IDs (truncated for matching)
VALID_KB_PREFIXES = [
    "03a11444567d11f1896b",
    "0970bc5e615311f1ae53",
    "14030fd54d3211f1845d",
    "402daf974d3211f1aa1a",
    "4bc37dfa5f5311f195c2",
    "5285b55a4d3111f1aa19",
    "83a1a6894d3211f19139",
    "84f29caf4d3111f1bb36",
    "a35c93a0544811f1b4e8",  # This was the correct full ID but 20-char prefix may not match
    "aef04506469b11f1bc55",
    "b3caf8aa4d3211f1b77b",
    "be9c2cca533511f1b51c",
    "c21b04494d3111f1a479",
    "d23e0644578211f19c3b",
    "d8bcd5454d3011f1bbd2",
    "eaa92f844d3111f1afcc",
    "f494f9d255de11f1b4e8",
    "fc495e114fff11f1b745",
]

# The full valid KB IDs from the DB
VALID_KB_FULL = {
    "03a11444567d11f1896b599a73f69758",
    "0970bc5e615311f1ae53b6fc2d1f7b40",
    "14030fd54d3211f1845d5e59b26d4a16",
    "402daf974d3211f1aa1ae12b5e399ca2",
    "4bc37dfa5f5311f195c2ba57b46b34b",
    "5285b55a4d3111f1aa1983f0b8ac6a3",
    "83a1a6894d3211f19139e3166e26d51e",
    "84f29caf4d3111f1bb36dc9460ac29c",
    "a35c93a0544811f1b4e8ea68843e46a8",
    "aef04506469b11f1bc5579aa46e0b465",
    "b3caf8aa4d3211f1b77b61b8e8d53eb",
    "be9c2cca533511f1b51c84b0ef8ae9ca",
    "c21b04494d3111f1a479d0c3b87d5e0c",
    "d23e0644578211f19c3b0dcbc585580a",
    "d8bcd5454d3011f1bbd2f06b5044a53",
    "eaa92f844d3111f1afcc5d80f1ed2f40",
    "f494f9d255de11f1b4e87f0c41b4a664",
    "fc495e114fff11f1b74541b2c8f914e7",
}

result = subprocess.run(
    ["docker", "exec", "docker-ragflow-cpu-1", "ps", "aux"],
    capture_output=True, text=True
)

killed = []
for line in result.stdout.splitlines():
    if "unified_crawler" not in line or "grep" in line or "tail" in line:
        continue
    # Extract KB ID from command line
    kb_match = re.search(r'--kb-id\s+(\S+)', line)
    pid_match = re.search(r'root\s+(\d+)', line)
    if not kb_match or not pid_match:
        continue
    kb = kb_match.group(1)
    pid = pid_match.group(1)
    if kb not in VALID_KB_FULL:
        # Also check if the first 20 chars match any valid prefix
        prefix_match = any(kb[:20] == v for v in VALID_KB_PREFIXES)
        # The invalid KB IDs were the full IDs, so check exactly
        print(f"Killing PID {pid}: KB={kb} (INVALID)")
        subprocess.run(["docker", "exec", "docker-ragflow-cpu-1", "kill", "-9", pid])
        killed.append(pid)
    # else: valid KB, let it run

print(f"\nKilled {len(killed)} processes with invalid KB IDs")
