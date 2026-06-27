import os

os.environ.update(
    {
        "AUTH_DISABLED": "true",
        "FIRESTORE_ENABLED": "false",
        "MOCK_AI": "true",
        "DEBUG_REWARD_AUTO_VERIFY": "true",
        "QUESTION_PATTERNS_PATH": "app/data/question_patterns.json",
        "FREE_PRACTICE_LIMIT": "3",
        "REWARD_PRACTICE_CREDITS": "1",
        "MAX_DAILY_REWARD_COUNT": "3",
    }
)
