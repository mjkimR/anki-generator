from abc import ABC, abstractmethod
from pathlib import Path
import re
import html

class BaseTTSProvider(ABC):
    """Abstract base class for TTS providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier string (e.g. 'azure', 'aivis', 'edge')."""
        pass

    @property
    @abstractmethod
    def render_version(self) -> str:
        """Renderer version tag for cache keying and audio tracking."""
        pass

    @staticmethod
    def reading_to_kana(back_reading: str) -> str:
        """Turns the validated bracket-furigana sentence into kana-only text with spaces preserved."""
        return re.sub(r'[^\s\[\]]+\[([^\]]+)\]', r'\1', back_reading or "").strip()

    @staticmethod
    def strip_markup(raw_html: str) -> str:
        """Strips HTML tags, decodes entities, and drops target markers (*)."""
        text = re.sub(r'<br\s*/?>', ' ', raw_html or "", flags=re.IGNORECASE)
        text = re.sub(r'<.*?>', '', text)
        text = html.unescape(text)
        return text.replace('*', '')

    @staticmethod
    def clean_html(raw_html: str) -> str:
        """Strip card markup and bracket readings from text for display/debugging."""
        text = BaseTTSProvider.strip_markup(raw_html)
        text = re.sub(r'\[[^\]]*\]', '', text)
        return text.strip()

    def metadata(self, voice: str) -> dict:
        return {
            "provider": self.provider_name,
            "voice": voice,
            "render_version": self.render_version,
        }

    @staticmethod
    def failure(message: str, metadata: dict | None = None, *, error_code: str,
                error_stage: str, retryable: bool, details: dict | None = None) -> dict:
        result = {
            "success": False,
            "error": message,
            "error_code": error_code,
            "error_stage": error_stage,
            "retryable": retryable,
            **(metadata or {}),
        }
        if details:
            result["error_details"] = details
        return result

    def provider_exception(self, exception: Exception, metadata: dict) -> dict:
        details = {
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
        }
        return self.failure(
            f"{self.provider_name.title()} TTS request raised {type(exception).__name__}: {str(exception)}",
            metadata, error_code=f"{self.provider_name}_exception", error_stage="provider_request",
            retryable=not isinstance(exception, (TypeError, ValueError)), details=details)

    @staticmethod
    def filesystem_failure(message: str, exception: Exception, metadata: dict, *, error_code: str, error_stage: str) -> dict:
        return BaseTTSProvider.failure(
            f"{message}: {type(exception).__name__}: {str(exception)}", metadata,
            error_code=error_code, error_stage=error_stage, retryable=False,
            details={"exception_type": type(exception).__name__,
                     "exception_message": str(exception)})

    def remove_partial_output(self, output_path: Path):
        try:
            Path(output_path).unlink(missing_ok=True)
        except OSError:
            pass

    @abstractmethod
    def prepare_text(self, raw_text: str, voice: str) -> str:
        """Prepare raw input text into provider-specific payload format."""
        pass

    @abstractmethod
    async def generate_speech(self, text: str, output_path: Path, voice: str) -> dict:
        """Synthesize speech and save to output_path asynchronously."""
        pass
