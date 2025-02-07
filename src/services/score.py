# src/services/score.py

class ScoreService:
    def __init__(self):
        self._score_cache = {}

    async def calculate_score(self, attempt_id: str, messages: list) -> float:
        """
        Placeholder scoring function that returns a random score between 0 and 10
        """
        if attempt_id in self._score_cache:
            return self._score_cache[attempt_id]
            
        # For now, just return a basic score based on number of messages
        score = min(len(messages), 10) * 0.7  # Simple scoring logic
        self._score_cache[attempt_id] = score
        return score

_score_service = None

def get_score_service():
    global _score_service
    if _score_service is None:
        _score_service = ScoreService()
    return _score_service