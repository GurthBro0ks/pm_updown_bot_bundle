"""
Utility functions for proof generation
"""
import json
from pathlib import Path
from datetime import datetime, timezone

PROOF_DIR = Path("/opt/slimy/pm_updown_bot_bundle/proofs")

def generate_proof(proof_id, data):
    """Generate a proof file"""
    PROOF_DIR.mkdir(exist_ok=True)
    proof_path = PROOF_DIR / f"{proof_id}.json"
    with open(proof_path, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    return str(proof_path)
