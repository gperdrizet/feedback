"""AI provider integration points for generating grading drafts."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


@dataclass
class AIDraftResult:
    feedback: str
    score: float | None
    provider_name: str
    model_name: str


class OpenAICompatibleProvider:
    """Promptly-backed OpenAI-compatible provider."""

    provider_name = "promptly-openai-compatible"

    def __init__(self):
        load_dotenv()
        self.base_url = os.getenv("FEEDBACK_AI_BASE_URL", "https://promptlyapi.com/v1")
        self.api_key = os.getenv("FEEDBACK_AI_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.model_name = os.getenv("FEEDBACK_AI_MODEL", "default")
        self.temperature = float(os.getenv("FEEDBACK_AI_TEMPERATURE", "0.2"))

        if not self.api_key:
            raise ValueError(
                "Missing FEEDBACK_AI_API_KEY (or OPENAI_API_KEY). "
                "Set it in environment or .env before generating drafts."
            )

        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def _artifact_summary(self, artifacts):
        lines = []
        for artifact in artifacts:
            path = artifact.local_path or artifact.source_url or "artifact"
            lines.append(f"- {path}")
        return "\n".join(lines) if lines else "- No downloaded artifacts available"

    def _read_text_samples(self, artifacts, max_files=3, max_chars_per_file=3000):
        samples = []
        for artifact in artifacts:
            if len(samples) >= max_files:
                break
            if not artifact.local_path:
                continue

            path = Path(artifact.local_path)
            if not path.exists() or path.is_dir():
                continue

            suffix = path.suffix.lower()
            if suffix not in {".py", ".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".html", ".js", ".ts"}:
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            samples.append(
                f"\n### File: {path.name}\n"
                f"{content[:max_chars_per_file]}\n"
            )

        return "\n".join(samples) if samples else "No text samples could be extracted."

    def _build_prompt(self, assignment_description, student_name, artifacts):
        artifact_summary = self._artifact_summary(artifacts)
        file_samples = self._read_text_samples(artifacts)
        return (
            "You are an instructional assistant grading a student assignment. "
            "Return concise, actionable, and respectful feedback in markdown. "
            "Include sections: Summary, Strengths, Improvements, Suggested Next Steps, and Proposed Score. "
            "If evidence is insufficient, say so explicitly.\n\n"
            f"Student: {student_name}\n\n"
            "Assignment description from Canvas:\n"
            f"{assignment_description[:8000]}\n\n"
            "Artifacts available:\n"
            f"{artifact_summary}\n\n"
            "Extracted text samples from submitted files:\n"
            f"{file_samples}\n"
        )

    @staticmethod
    def _extract_content(response):
        if not response.choices:
            raise ValueError("AI response contained no choices.")

        message = response.choices[0].message
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
            return "\n".join(parts).strip()
        return ""

    def generate_feedback(self, assignment_description, student_name, artifacts):
        prompt = self._build_prompt(
            assignment_description=assignment_description,
            student_name=student_name,
            artifacts=artifacts,
        )

        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict but constructive instructor assistant.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        feedback = self._extract_content(response)
        if not feedback:
            raise ValueError("AI response did not contain message content.")

        return AIDraftResult(
            feedback=feedback,
            score=None,
            provider_name=self.provider_name,
            model_name=getattr(response, "model", self.model_name),
        )
