"""
VEBB-AI: Semantic Caching Module
Stores and retrieves Gemini decisions based on market state similarity.
Reduces API costs and enables high-speed backtesting.
"""

import sqlite3
import json
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

class SemanticCache:
    """
    A lightweight vector-similarity cache using SQLite and Numpy.
    """
    def __init__(self, db_path: str = "gemini_cache.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database for semantic storage."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vector BLOB,
                    decision TEXT,
                    metadata TEXT,
                    decision_type TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def _create_vector(self, metrics: Dict[str, Any]) -> np.ndarray:
        """
        Convert market metrics into a normalized feature vector.
        Features: [GK_Vol, Hurst, OBI, OFI, Delta_Norm, Range_Pos]
        """
        # Delta normalization (estimate)
        delta_norm = np.tanh(metrics.get("delta", 0) / 100.0)
        
        vector = [
            metrics.get("gk_vol", 0) * 100,  # Scale vol
            metrics.get("hurst", 0.5),      # 0 to 1
            metrics.get("obi", 0),          # -1 to 1
            metrics.get("ofi", 0),          # -1 to 1
            delta_norm,                     # -1 to 1
            metrics.get("range_pos", 0.5)   # 0 to 1 (calculated position in VA)
        ]
        return np.array(vector, dtype=np.float32)

    def find_similar(self, metrics: Dict[str, Any], threshold: float = 0.98, decision_type: str = "entry", max_age_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Search for a similar market state in the cache.
        Returns the decision if similarity > threshold and decision is fresh enough.
        """
        query_vec = self._create_vector(metrics)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT vector, decision, timestamp FROM semantic_cache WHERE decision_type = ?", 
                (decision_type,)
            )
            rows = cursor.fetchall()

        best_sim = -1.0
        best_decision = None

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc) if max_age_seconds else None

        for vec_blob, decision_json, timestamp_str in rows:
            # 1. Check Age (Phase 54 Freshness Mandate)
            if max_age_seconds and timestamp_str:
                try:
                    # SQLite timestamp format: YYYY-MM-DD HH:MM:SS
                    # Default CURRENT_TIMESTAMP is UTC
                    ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    age = (now - ts).total_seconds()
                    if age > max_age_seconds:
                        continue # Decision too old
                except Exception:
                    pass

            stored_vec = np.frombuffer(vec_blob, dtype=np.float32)
            
            # Cosine Similarity
            norm_q = np.linalg.norm(query_vec)
            norm_s = np.linalg.norm(stored_vec)
            
            if norm_q == 0 or norm_s == 0:
                similarity = 0
            else:
                similarity = np.dot(query_vec, stored_vec) / (norm_q * norm_s)
            
            if similarity > best_sim:
                best_sim = similarity
                best_decision = decision_json

        if best_sim >= threshold:
            print(f"  [Cache] Semantic Hit ({decision_type})! Similarity: {best_sim:.4f}")
            return json.loads(best_decision)
        
        return None

    def store(self, metrics: Dict[str, Any], decision: Dict[str, Any], decision_type: str = "entry"):
        """Store a new Gemini decision in the semantic cache."""
        vector = self._create_vector(metrics)
        vec_blob = vector.tobytes()
        decision_json = json.dumps(decision)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO semantic_cache (vector, decision, metadata, decision_type) VALUES (?, ?, ?, ?)",
                (vec_blob, decision_json, json.dumps(metrics), decision_type)
            )
            conn.commit()
        # print(f"  [Cache] Stored new decision.")

if __name__ == "__main__":
    # Quick test
    cache = SemanticCache("test_cache.db")
    m1 = {"gk_vol": 0.002, "hurst": 0.45, "obi": -0.85, "ofi": -0.5, "delta": -120, "range_pos": 0.8}
    d1 = {"action": "GO_SHORT", "confidence": 0.9}
    
    cache.store(m1, d1)
    
    # Slightly different state
    m2 = {"gk_vol": 0.0021, "hurst": 0.44, "obi": -0.84, "ofi": -0.48, "delta": -115, "range_pos": 0.79}
    hit = cache.find_similar(m2, threshold=0.98)
    print(f"Similarity search result: {hit}")
