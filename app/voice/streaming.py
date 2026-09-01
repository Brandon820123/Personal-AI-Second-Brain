"""Sentence segmentation and ordered local speech workers for streaming TTS."""

from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


_QUEUE_STOP = object()
_CLOSING_CHARACTERS = "\"'”’）)]}】」』"
_CHINESE_BOUNDARIES = "。！？；"
_ENGLISH_ABBREVIATIONS = {
    "dr",
    "e.g",
    "etc",
    "fig",
    "i.e",
    "jr",
    "mr",
    "mrs",
    "ms",
    "no",
    "prof",
    "sr",
    "vs",
}


def _noop(*unused_arguments):
    del unused_arguments


class StreamingSentenceSegmenter:
    """Turn arbitrary streamed token chunks into useful natural sentences."""

    def __init__(self, minimum_units=8):
        self.minimum_units = max(1, int(minimum_units))
        self._buffer = ""

    @property
    def pending_text(self):
        return self._buffer

    def feed(self, text):
        """Append a token chunk and return every newly completed sentence."""
        chunk = str(text or "")

        if not chunk:
            return []

        self._buffer += chunk
        return self._extract_completed(final=False)

    def finish(self):
        """Flush a useful final fragment when model generation completes."""
        sentences = self._extract_completed(final=True)
        remainder = self._buffer.strip()
        self._buffer = ""

        if remainder and self._is_useful(remainder):
            sentences.append(remainder)

        return sentences

    def clear(self):
        """Discard an interrupted response fragment immediately."""
        self._buffer = ""

    def _extract_completed(self, final):
        completed = []
        scan_from = 0

        while self._buffer:
            boundary_end = self._next_boundary(scan_from, final)

            if boundary_end is None:
                break

            candidate = self._buffer[:boundary_end].strip()
            scan_from = boundary_end

            if not self._is_useful(candidate):
                continue

            completed.append(candidate)
            self._buffer = self._buffer[boundary_end:]
            scan_from = 0

        return completed

    def _next_boundary(self, start, final):
        index = max(0, int(start))

        while index < len(self._buffer):
            character = self._buffer[index]

            if character in _CHINESE_BOUNDARIES or character in "!?":
                return self._include_trailing_punctuation(index + 1)

            if character == "." and self._period_is_boundary(index, final):
                return self._include_trailing_punctuation(index + 1)

            index += 1

        return None

    def _include_trailing_punctuation(self, end):
        while end < len(self._buffer):
            character = self._buffer[end]

            if character in ".!?。！？" or character in _CLOSING_CHARACTERS:
                end += 1
                continue

            break

        return end

    def _period_is_boundary(self, index, final):
        previous_character = self._buffer[index - 1] if index else ""
        next_character = (
            self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
        )

        if previous_character.isdigit() and next_character.isdigit():
            return False

        if next_character == ".":
            return False

        if not next_character and not final:
            return False

        prefix = self._buffer[:index]
        word_match = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)$", prefix)

        if word_match:
            last_word = word_match.group(1).casefold().rstrip(".")

            if last_word in _ENGLISH_ABBREVIATIONS or len(last_word) == 1:
                return False

        if re.search(r"(?:\b[A-Za-z]\.)+[A-Za-z]$", prefix):
            return False

        following_index = index + 1

        while (
            following_index < len(self._buffer)
            and self._buffer[following_index] in _CLOSING_CHARACTERS
        ):
            following_index += 1

        if following_index < len(self._buffer):
            following_character = self._buffer[following_index]

            if not following_character.isspace() and following_character.isalnum():
                return False

        return True

    def _is_useful(self, text):
        chinese_count = len(re.findall(r"[\u3400-\u9fff]", text))
        other_alphanumeric = sum(
            character.isalnum()
            and not ("\u3400" <= character <= "\u9fff")
            for character in text
        )
        return chinese_count * 2 + other_alphanumeric >= self.minimum_units


@dataclass(frozen=True)
class SpeechRequest:
    """One sentence and its immutable local playback context."""

    session_id: int
    sequence: int
    text: str
    profile: dict
    language_id: str
    device: object
    enqueued_at: float
    cancel_event: threading.Event = field(repr=False, compare=False)


@dataclass(frozen=True)
class PreparedSpeech:
    """A synthesized WAV waiting for its ordered playback turn."""

    request: SpeechRequest
    path: Path
    voice_id: str


@dataclass(frozen=True)
class VoicePreparation:
    """A cancellable request to warm a voice without creating audio."""

    session_id: int
    profile: dict
    language_id: str
    cancel_event: threading.Event = field(repr=False, compare=False)


@dataclass
class _SpeechSession:
    session_id: int
    cancel_event: threading.Event = field(default_factory=threading.Event)
    accepting: bool = True
    active: bool = False
    drained: bool = False
    pending_count: int = 0
    next_sequence: int = 0


class ThreadedSpeechQueue:
    """Synthesize and play sentences through separate ordered worker threads."""

    def __init__(
        self,
        tts_engine,
        audio_player,
        on_activity=_noop,
        on_started=_noop,
        on_warning=_noop,
        on_drained=_noop,
    ):
        self.tts_engine = tts_engine
        self.audio_player = audio_player
        self._on_activity = on_activity
        self._on_started = on_started
        self._on_warning = on_warning
        self._on_drained = on_drained
        self._synthesis_queue = queue.Queue()
        self._playback_queue = queue.Queue()
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._session = None
        self._next_session_id = 0
        self._synthesis_thread = threading.Thread(
            target=self._synthesis_loop,
            name="local-tts-synthesis",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=self._playback_loop,
            name="local-tts-playback",
            daemon=True,
        )
        self._synthesis_thread.start()
        self._playback_thread.start()

    def start_session(self):
        """Cancel old speech and return a new generation-safe session ID."""
        self.cancel()

        with self._lock:
            self._next_session_id += 1
            self._session = _SpeechSession(self._next_session_id)
            return self._session.session_id

    def enqueue(self, session_id, text, profile, language_id, device=None):
        """Append one sentence without waiting for synthesis or playback."""
        content = str(text or "").strip()

        if not content:
            return False

        notify_active = False

        with self._lock:
            session = self._matching_session(session_id)

            if session is None or not session.accepting or self._shutdown.is_set():
                return False

            request = SpeechRequest(
                session_id=session.session_id,
                sequence=session.next_sequence,
                text=content,
                profile=dict(profile),
                language_id=str(language_id),
                device=device,
                enqueued_at=time.perf_counter(),
                cancel_event=session.cancel_event,
            )
            session.next_sequence += 1
            session.pending_count += 1

            if not session.active:
                session.active = True
                notify_active = True

        self._synthesis_queue.put(request)

        if notify_active:
            self._notify(self._on_activity, session_id, True)

        return True

    def prepare_voice(self, session_id, profile, language_id):
        """Warm the likely voice while Ollama is still generating tokens."""
        with self._lock:
            session = self._matching_session(session_id)

            if session is None or self._shutdown.is_set():
                return False

            preparation = VoicePreparation(
                session_id=session.session_id,
                profile=dict(profile),
                language_id=str(language_id),
                cancel_event=session.cancel_event,
            )

        self._synthesis_queue.put(preparation)
        return True

    def finish_session(self, session_id):
        """Mark model generation done and emit drained after final playback."""
        notify_drained = False

        with self._lock:
            session = self._matching_session(session_id)

            if session is None:
                return

            session.accepting = False

            if session.pending_count == 0 and not session.drained:
                session.drained = True
                notify_drained = True

        if notify_drained:
            self._notify(self._on_drained, session_id)

    def is_active(self, session_id=None):
        with self._lock:
            session = self._session

            if session is None:
                return False
            if session_id is not None and session.session_id != session_id:
                return False
            return session.active and session.pending_count > 0

    def cancel(self):
        """Immediately stop output and invalidate every queued old request."""
        notify_session_id = None

        with self._lock:
            session = self._session
            self._session = None

            if session is not None:
                session.cancel_event.set()

                if session.active:
                    notify_session_id = session.session_id

        self.audio_player.stop()
        self._discard_queued_items(self._synthesis_queue, delete_audio=False)
        self._discard_queued_items(self._playback_queue, delete_audio=True)

        if notify_session_id is not None:
            self._notify(self._on_activity, notify_session_id, False)

    def shutdown(self, wait=False, timeout=2.0):
        """Stop private workers and release cached local voice resources."""
        if self._shutdown.is_set():
            return

        self._shutdown.set()
        self.cancel()
        self._synthesis_queue.put(_QUEUE_STOP)
        self._playback_queue.put(_QUEUE_STOP)

        if wait:
            self._synthesis_thread.join(timeout=max(0.0, float(timeout)))
            self._playback_thread.join(timeout=max(0.0, float(timeout)))

    def _matching_session(self, session_id):
        session = self._session

        if session is None or session.session_id != session_id:
            return None
        if session.cancel_event.is_set():
            return None
        return session

    def _request_is_current(self, request):
        if request.cancel_event.is_set() or self._shutdown.is_set():
            return False

        with self._lock:
            return self._matching_session(request.session_id) is not None

    def _synthesis_loop(self):
        try:
            while not self._shutdown.is_set():
                item = self._synthesis_queue.get()

                if item is _QUEUE_STOP:
                    break
                if not self._request_is_current(item):
                    continue

                if isinstance(item, VoicePreparation):
                    try:
                        self.tts_engine.prepare(item.profile, item.language_id)
                    except Exception:
                        pass
                    continue

                try:
                    result = self.tts_engine.synthesize(
                        item.text,
                        item.profile,
                        item.language_id,
                    )
                    prepared = PreparedSpeech(
                        request=item,
                        path=Path(result["path"]),
                        voice_id=str(result.get("voice_id", "local")),
                    )
                except Exception as error:
                    if self._request_is_current(item):
                        self._notify(
                            self._on_warning,
                            item.session_id,
                            str(error),
                        )
                        self._complete_request(item)
                    continue

                if not self._request_is_current(item):
                    prepared.path.unlink(missing_ok=True)
                    continue

                self._playback_queue.put(prepared)
        finally:
            try:
                self.tts_engine.unload()
            except Exception:
                pass

    def _playback_loop(self):
        try:
            while not self._shutdown.is_set():
                item = self._playback_queue.get()

                if item is _QUEUE_STOP:
                    break
                if not self._request_is_current(item.request):
                    item.path.unlink(missing_ok=True)
                    continue

                request = item.request
                self._notify(
                    self._on_started,
                    request.session_id,
                    request.sequence,
                    request.text,
                    request.enqueued_at,
                    item.voice_id,
                )

                try:
                    completed = self.audio_player.play(
                        item.path,
                        request.device,
                        cancel_event=request.cancel_event,
                    )
                except Exception as error:
                    completed = False

                    if self._request_is_current(request):
                        self._notify(
                            self._on_warning,
                            request.session_id,
                            str(error),
                        )

                if not self._request_is_current(request):
                    continue

                if completed:
                    pause_seconds = min(
                        2.0,
                        max(0.0, float(request.profile.get("pause_ms", 0)))
                        / 1000.0,
                    )
                    request.cancel_event.wait(pause_seconds)

                if self._request_is_current(request):
                    self._complete_request(request)
        finally:
            try:
                self.audio_player.unload()
            except Exception:
                pass

    def _complete_request(self, request):
        notify_inactive = False
        notify_drained = False

        with self._lock:
            session = self._matching_session(request.session_id)

            if session is None:
                return

            session.pending_count = max(0, session.pending_count - 1)

            if session.pending_count == 0 and session.active:
                session.active = False
                notify_inactive = True

            if (
                session.pending_count == 0
                and not session.accepting
                and not session.drained
            ):
                session.drained = True
                notify_drained = True

        if notify_inactive:
            self._notify(self._on_activity, request.session_id, False)
        if notify_drained:
            self._notify(self._on_drained, request.session_id)

    @staticmethod
    def _discard_queued_items(work_queue, delete_audio):
        while True:
            try:
                item = work_queue.get_nowait()
            except queue.Empty:
                return

            if item is _QUEUE_STOP:
                work_queue.put(_QUEUE_STOP)
                return
            if delete_audio and isinstance(item, PreparedSpeech):
                item.path.unlink(missing_ok=True)

    @staticmethod
    def _notify(callback, *arguments):
        try:
            callback(*arguments)
        except Exception:
            pass
