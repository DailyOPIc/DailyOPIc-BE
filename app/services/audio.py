from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.models.api import AudioMetrics


SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")

# 앱이 180초에서 녹음을 멈춰도 컨테이너 길이는 그보다 조금 길게 찍힌다.
# AAC 인코더 패딩 때문이다(측정: 180.000초 원본 → 180.053초 m4a). 여기에 1초
# 단위 타이머와 엔진 정지 지연이 더해진다. 정상 길이 녹음을 "너무 길다"로
# 되돌리지 않도록 짧은 허용치를 둔다. 진짜 초과 녹음은 그대로 막힌다.
DURATION_TOLERANCE_SECONDS = 2.0


class AudioValidationError(ValueError):
    pass


class AudioMetricsService:
    def __init__(self, *, max_bytes: int, max_seconds: int) -> None:
        self._max_bytes = max_bytes
        self._max_seconds = max_seconds

    async def analyze(self, upload: UploadFile | None, transcript: str) -> AudioMetrics:
        words = len(re.findall(r"\b[A-Za-z']+\b", transcript))
        if upload is None:
            estimated = max(1.0, words / 130 * 60)
            return AudioMetrics(
                durationSeconds=estimated,
                speakingSeconds=estimated,
                silenceRatio=0,
                wordsPerMinute=words / estimated * 60,
                isEstimated=True,
            )

        suffix = Path(upload.filename or "answer.m4a").suffix or ".m4a"
        content = await upload.read(self._max_bytes + 1)
        if len(content) > self._max_bytes:
            raise AudioValidationError("audio file is too large")
        if not content:
            raise AudioValidationError("audio file is empty")

        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(content)
            handle.flush()
            return await asyncio.to_thread(self._analyze_path, Path(handle.name), words)

    def _analyze_path(self, path: Path, words: int) -> AudioMetrics:
        try:
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            duration = float(probe.stdout.strip())
            estimated = False
        except (OSError, ValueError, subprocess.SubprocessError):
            # 길이를 재지 못하면 전사 길이로 추정한다. 추정이라는 사실을 남긴다.
            duration = max(1.0, words / 130 * 60)
            estimated = True

        if duration > self._max_seconds + DURATION_TOLERANCE_SECONDS:
            raise AudioValidationError(f"audio must be {self._max_seconds} seconds or shorter")

        silence_seconds = 0.0
        try:
            process = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    str(path),
                    "-af",
                    "silencedetect=noise=-40dB:d=0.5",
                    "-f",
                    "null",
                    "-",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            open_start: float | None = None
            for line in process.stderr.splitlines():
                start = SILENCE_START.search(line)
                if start:
                    open_start = float(start.group(1))
                end = SILENCE_END.search(line)
                if end and open_start is not None:
                    silence_seconds += max(0.0, float(end.group(1)) - open_start)
                    open_start = None
            if open_start is not None:
                silence_seconds += max(0.0, duration - open_start)
        except (OSError, subprocess.SubprocessError):
            silence_seconds = 0.0
            estimated = True

        speaking = max(0.5, duration - min(duration, silence_seconds))
        return AudioMetrics(
            durationSeconds=round(duration, 2),
            speakingSeconds=round(speaking, 2),
            silenceRatio=round(min(1.0, silence_seconds / max(duration, 0.5)), 3),
            wordsPerMinute=round(words / speaking * 60, 1),
            isEstimated=estimated,
        )
