from datetime import datetime
from typing import Dict, List
import hashlib

class AnalyticsTracker:
    def __init__(self):
        self.usage_data: List[Dict] = []
    
    def log_usage(self, user_id: int, tokens_used: int, report_type: str = "weekly"):
        # Anonymize user ID
        anonymized_id = hashlib.sha256(str(user_id).encode()).hexdigest()[:16]
        
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "anonymized_user_id": anonymized_id,
            "tokens_used": tokens_used,
            "report_type": report_type
        }
        
        self.usage_data.append(entry)
        
        # Keep only last 1000 entries in memory
        if len(self.usage_data) > 1000:
            self.usage_data = self.usage_data[-1000:]
    
    def get_usage_stats(self) -> Dict:
        total_tokens = sum(entry["tokens_used"] for entry in self.usage_data)
        total_requests = len(self.usage_data)
        
        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "average_tokens_per_request": total_tokens / total_requests if total_requests > 0 else 0
        }

analytics_tracker = AnalyticsTracker()
