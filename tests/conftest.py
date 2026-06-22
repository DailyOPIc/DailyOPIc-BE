import os

os.environ.update(
    {
        "AUTH_DISABLED": "true",
        "FIRESTORE_ENABLED": "false",
        "MOCK_AI": "true",
        "DEBUG_REWARD_AUTO_VERIFY": "true",
        "QUESTION_PATTERNS_PATH": "../opic_mobile/questions.json",
    }
)
