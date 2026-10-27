"""
Utility functions for hashing, sampling, and data processing.
"""
import hashlib
import random


def compute_path_id(event_types: list[str]) -> str:
    """
    Computes a deterministic path_id MD5 hash from an ordered list of event_type strings.
    No run-specific salt, timestamp, or dataset identifier is included in the hash.
    Example: ['view_product', 'add_to_cart', 'purchase'] -> 'b5a79f32c18d42e9...'
    """
    path_signature = "|".join(event_types)
    return hashlib.md5(path_signature.encode("utf-8")).hexdigest()


def sample_pareto_click_position(results_returned: int, random_state: random.Random = None) -> int:
    """
    Samples result_position_clicked based on a Pareto distribution:
    80% of clicks target positions 1, 2, or 3.
    20% of clicks target positions 4 to results_returned (if results_returned > 3).
    """
    rng = random_state or random
    if results_returned <= 3:
        return rng.randint(1, max(1, results_returned))

    if rng.random() < 0.80:
        return rng.randint(1, 3)
    else:
        return rng.randint(4, results_returned)
