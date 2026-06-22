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
        except (OSError, ValueError, subprocess.SubprocessError):
            duration = max(1.0, words / 130 * 60)

        if duration > self._max_seconds:
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

        speaking = max(0.5, duration - min(duration, silence_seconds))
        return AudioMetrics(
            durationSeconds=round(duration, 2),
            speakingSeconds=round(speaking, 2),
            silenceRatio=round(min(1.0, silence_seconds / max(duration, 0.5)), 3),
            wordsPerMinute=round(words / speaking * 60, 1),
        )
