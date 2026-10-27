import hashlib
from pathlib import Path


def compute_target_config_hash(targets_csv: Path) -> str:
    """SHA-256[:8] hex of target CSV byte content only."""
    return hashlib.sha256(targets_csv.read_bytes()).hexdigest()[:8]
