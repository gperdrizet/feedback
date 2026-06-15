"""AI provider integration points for generating grading drafts."""

import json
import os
import re
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
    prompt_diagnostics: dict


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
        self._last_sampling_diagnostics = {}

    def _artifact_summary(self, artifacts):
        lines = []
        for artifact in artifacts:
            path = artifact.local_path or artifact.source_url or "artifact"
            lines.append(f"- {path}")
        return "\n".join(lines) if lines else "- No downloaded artifacts available"

    @staticmethod
    def _positive_int_env(name, default):
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            parsed = int(raw)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _head_tail_sample(text, max_chars):
        if len(text) <= max_chars:
            return text, False

        # Preserve both early and late content so init/setup and final execution calls are visible.
        head_chars = int(max_chars * 0.7)
        tail_chars = max_chars - head_chars
        head = text[:head_chars].rstrip()
        tail = text[-tail_chars:].lstrip()
        return (
            f"{head}\n\n"
            "[... truncated middle content ...]\n\n"
            f"{tail}"
        ), True

    @staticmethod
    def _notebook_text_sample(content, max_chars_per_file):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None

        cells = payload.get("cells") if isinstance(payload, dict) else None
        if not isinstance(cells, list) or not cells:
            return None

        parts = []
        for index, cell in enumerate(cells, start=1):
            if not isinstance(cell, dict):
                continue

            cell_type = cell.get("cell_type", "unknown")
            source = cell.get("source", "")
            if isinstance(source, list):
                text = "".join(source)
            else:
                text = str(source or "")

            text = text.strip()
            if not text:
                continue

            parts.append(f"[Cell {index} - {cell_type}]\n{text}")

        if not parts:
            return "Notebook file found, but cells contained no readable source text.", False

        notebook_text = "\n\n".join(parts)
        return OpenAICompatibleProvider._head_tail_sample(notebook_text, max_chars_per_file)

    def _read_text_samples(self, artifacts, max_files=None, max_chars_per_file=None, max_total_chars=None):
        if max_files is None:
            max_files = self._positive_int_env("FEEDBACK_MAX_PROMPT_FILES", 8)
        if max_chars_per_file is None:
            max_chars_per_file = self._positive_int_env("FEEDBACK_MAX_PROMPT_FILE_CHARS", 24000)
        if max_total_chars is None:
            max_total_chars = self._positive_int_env("FEEDBACK_MAX_PROMPT_TOTAL_CHARS", 52000)

        samples = []
        total_chars = 0
        diagnostics = {
            "max_files": max_files,
            "max_chars_per_file": max_chars_per_file,
            "max_total_chars": max_total_chars,
            "files_sampled": 0,
            "total_chars_used": 0,
            "truncated": False,
            "truncated_file_count": 0,
            "truncated_files": [],
        }

        for artifact in artifacts:
            if len(samples) >= max_files or total_chars >= max_total_chars:
                diagnostics["truncated"] = True
                break
            if not artifact.local_path:
                continue

            path = Path(artifact.local_path)
            if not path.exists() or path.is_dir():
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            suffix = path.suffix.lower()
            is_text_suffix = suffix in {
                ".py",
                ".txt",
                ".md",
                ".json",
                ".yaml",
                ".yml",
                ".csv",
                ".html",
                ".js",
                ".ts",
            }
            is_notebook_candidate = suffix == ".ipynb" or '"cells"' in content

            extracted = None
            extracted_was_truncated = False
            if is_notebook_candidate:
                extracted, extracted_was_truncated = self._notebook_text_sample(content, max_chars_per_file)

            if extracted is None and not is_text_suffix:
                continue

            remaining_chars = max_total_chars - total_chars
            if remaining_chars <= 0:
                break

            file_char_budget = min(max_chars_per_file, remaining_chars)
            sample_text = extracted if extracted is not None else content
            sample_text, was_truncated_for_budget = self._head_tail_sample(sample_text, file_char_budget)
            was_truncated = extracted_was_truncated or was_truncated_for_budget

            block = (
                f"\n### File: {path.name}\n"
                f"{sample_text}\n"
            )
            samples.append(block)
            total_chars += len(block)
            diagnostics["files_sampled"] += 1

            if was_truncated:
                diagnostics["truncated"] = True
                diagnostics["truncated_file_count"] += 1
                diagnostics["truncated_files"].append(path.name)

        diagnostics["total_chars_used"] = total_chars
        self._last_sampling_diagnostics = diagnostics

        return "\n".join(samples) if samples else "No text samples could be extracted."

    @staticmethod
    def _build_rubric_block(rubric):
        """Return a plain-text rubric section for the prompt, or an empty string."""
        if not rubric:
            return ""

        lines = ["Scoring Rubric:", ""]
        for criterion in rubric:
            lines.append(f"Criterion: {criterion['name']}")
            for level in criterion.get("levels", []):
                lines.append(f"  - {level['points']} pts: {level['description']}")
            lines.append("")
        return "\n".join(lines)

    def _build_prompt(self, assignment_description, student_name, artifacts, rubric=None, extra_instructions=None):
        artifact_summary = self._artifact_summary(artifacts)
        file_samples = self._read_text_samples(artifacts)
        rubric_block = self._build_rubric_block(rubric)

        if rubric_block:
            scoring_instruction = (
                "Structure your response in two parts. "
                "Part 1 — Narrative feedback: write specific, substantive comments about this "
                "particular submission. Include the following sections, each as its own paragraph "
                "or list: Summary (what the student did overall), Strengths (specific things done "
                "well with evidence from the submission), Areas for Improvement (concrete, "
                "actionable suggestions tied to the work), and Suggested Next Steps. "
                "Part 2 — Score breakdown: apply the rubric below. For each criterion, select "
                "the scale level that best matches the submission and note the point value. "
                "Present this as an HTML table with columns Criterion, Selected Level Description, "
                "and Points, followed by a Total row with the summed score. "
                "Use only these HTML tags: <p>, <strong>, <em>, <ul>, <ol>, <li>, <table>, "
                "<thead>, <tbody>, <tr>, <th>, <td>. "
                "Do not use markdown formatting."
            )
        else:
            scoring_instruction = (
                "Return concise, actionable, and respectful feedback as HTML only. "
                "Use simple tags only: <p>, <strong>, <em>, <ul>, <ol>, <li>. "
                "Do not use markdown formatting. "
                "Include sections: Summary, Strengths, Improvements, Suggested Next Steps, "
                "and Proposed Score."
            )

        rubric_section = f"\n{rubric_block}\n" if rubric_block else ""

        extra_section = (
            f"\nAdditional instructor guidance for this assignment:\n{extra_instructions.strip()}\n"
            if extra_instructions and extra_instructions.strip()
            else ""
        )

        return (
            f"You are an instructional assistant grading a student assignment. "
            f"{scoring_instruction} "
            f"If evidence is insufficient, say so explicitly.\n\n"
            f"Student: {student_name}\n\n"
            f"Assignment description from Canvas:\n"
            f"{assignment_description[:8000]}\n"
            f"{rubric_section}"
            f"{extra_section}\n"
            f"Artifacts available:\n"
            f"{artifact_summary}\n\n"
            f"Extracted text samples from submitted files:\n"
            f"{file_samples}\n"
        )

    def _build_submission_context(self, assignment_description, student_name, artifacts, rubric=None, extra_instructions=None):
        artifact_summary = self._artifact_summary(artifacts)
        file_samples = self._read_text_samples(artifacts)
        rubric_block = self._build_rubric_block(rubric)
        rubric_section = f"\n{rubric_block}\n" if rubric_block else ""
        extra_section = (
            f"\nAdditional instructor guidance for this assignment:\n{extra_instructions.strip()}\n"
            if extra_instructions and extra_instructions.strip()
            else ""
        )

        return (
            f"Student: {student_name}\n\n"
            "Assignment description from Canvas:\n"
            f"{assignment_description[:8000]}\n"
            f"{rubric_section}"
            f"{extra_section}\n"
            "Artifacts available:\n"
            f"{artifact_summary}\n\n"
            "Extracted text samples from submitted files:\n"
            f"{file_samples}\n"
        )

    def _run_generation_pass(self, system_prompt, user_prompt):
        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )
        content = self._extract_content(response)
        if not content:
            raise ValueError("AI pass returned no content.")
        return content, getattr(response, "model", self.model_name)

    def _generate_feedback_with_detailed_passes(
        self,
        assignment_description,
        student_name,
        artifacts,
        rubric=None,
        extra_instructions=None,
    ):
        context_block = self._build_submission_context(
            assignment_description=assignment_description,
            student_name=student_name,
            artifacts=artifacts,
            rubric=rubric,
            extra_instructions=extra_instructions,
        )

        evidence_prompt = (
            "Extract factual evidence from the submission context below. "
            "Return concise plain text under exactly these headings: "
            "Submission Summary, Confirmed Strength Evidence, Confirmed Issue Evidence, "
            "Missing or Unclear Evidence. Keep each section short and specific.\n\n"
            f"{context_block}"
        )
        evidence_text, model_name = self._run_generation_pass(
            "You are a careful evaluator. Only report claims directly supported by evidence.",
            evidence_prompt,
        )

        evaluation_prompt = (
            "Using only the evidence below, produce an evaluation plan in plain text. "
            "Use headings: Strengths to Reinforce, Priority Improvements, Suggested Next Actions, "
            "Scoring Notes. Keep recommendations concrete and assignment-focused.\n\n"
            "Evidence:\n"
            f"{evidence_text}\n\n"
            "Original submission context:\n"
            f"{context_block}"
        )
        evaluation_text, model_name = self._run_generation_pass(
            "You are an instructional coach creating practical feedback plans.",
            evaluation_prompt,
        )

        has_rubric = bool(rubric)
        if has_rubric:
            html_structure = (
                "Produce HTML using only: <p>, <strong>, <em>, <ul>, <ol>, <li>, <table>, <thead>, <tbody>, <tr>, <th>, <td>. "
                "Structure sections in order: Summary, Strengths, Areas for Improvement, Suggested Next Steps, Score Breakdown. "
                "For Score Breakdown include a table with columns Criterion, Selected Level Description, and Points, then a Total row. "
                "Do not use markdown."
            )
        else:
            html_structure = (
                "Produce HTML using only: <p>, <strong>, <em>, <ul>, <ol>, <li>. "
                "Structure sections in order: Summary, Strengths, Improvements, Suggested Next Steps, Proposed Score. "
                "Do not use markdown."
            )

        narrative_prompt = (
            "Create instructor-ready student feedback from the plan and evidence below. "
            f"{html_structure}"
            " If evidence is insufficient, explicitly say so in the relevant section.\n\n"
            "Evaluation plan:\n"
            f"{evaluation_text}\n\n"
            "Evidence:\n"
            f"{evidence_text}\n\n"
            "Original submission context:\n"
            f"{context_block}"
        )
        feedback_html, model_name = self._run_generation_pass(
            "You are a strict but constructive instructor assistant writing clean HTML feedback.",
            narrative_prompt,
        )

        consistency_prompt = (
            "Check and revise the HTML feedback below for consistency with the evidence and evaluation plan. "
            "Keep the same structure, remove unsupported claims, and improve clarity. "
            "Return only revised HTML using the same allowed tags.\n\n"
            "Evidence:\n"
            f"{evidence_text}\n\n"
            "Evaluation plan:\n"
            f"{evaluation_text}\n\n"
            "Draft HTML feedback:\n"
            f"{feedback_html}\n"
        )
        consistent_feedback_html, model_name = self._run_generation_pass(
            "You are a strict quality reviewer for instructional feedback.",
            consistency_prompt,
        )

        return consistent_feedback_html, model_name

    def _format_cohort_feedback_block(self, feedback_entries, max_entries=80, max_chars_each=1200):
        if not feedback_entries:
            return "No student feedback entries are available."

        lines = []
        for idx, entry in enumerate(feedback_entries[:max_entries], start=1):
            feedback = (entry.get("feedback") or "").strip()
            if not feedback:
                continue
            score = entry.get("score")
            score_text = str(score) if score is not None else "n/a"
            student = entry.get("student_name") or f"Student {idx}"
            status = entry.get("review_status") or "unknown"
            snippet = feedback[:max_chars_each]
            lines.append(
                f"Student: {student}\n"
                f"Review status: {status}\n"
                f"Score: {score_text}\n"
                f"Feedback:\n{snippet}\n"
            )

        return "\n---\n".join(lines) if lines else "No student feedback entries are available."

    def generate_cohort_summary(self, assignment_name, assignment_description, extra_instructions, feedback_entries):
        cohort_block = self._format_cohort_feedback_block(feedback_entries)
        guidance_block = extra_instructions.strip() if extra_instructions else ""

        analysis_prompt = (
            "You are analyzing cohort-level student feedback outcomes for an instructor. "
            "Based on the assignment context and per-student feedback, extract themes and patterns. "
            "Return plain text with concise bullets under these headings exactly: "
            "Observed Strength Patterns, Recurring Mistakes, Frequent Concept Gaps, "
            "Submission Quality Distribution, and Instructor Follow-up Priorities.\n\n"
            f"Assignment: {assignment_name}\n\n"
            "Assignment description:\n"
            f"{assignment_description[:10000]}\n\n"
            f"Additional instructor instructions:\n{guidance_block or 'None'}\n\n"
            "Per-student generated feedback entries:\n"
            f"{cohort_block}\n"
        )

        analysis_response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "You are a careful instructional analyst.",
                },
                {
                    "role": "user",
                    "content": analysis_prompt,
                },
            ],
        )
        analysis_text = self._extract_content(analysis_response)
        if not analysis_text:
            raise ValueError("Cohort analysis pass returned no content.")

        synthesis_prompt = (
            "Create an instructor-facing cohort summary from the analysis below. "
            "Output valid HTML only using: <p>, <strong>, <em>, <ul>, <ol>, <li>, <table>, <thead>, <tbody>, <tr>, <th>, <td>. "
            "Use these sections in order: Cohort Snapshot, Common Strengths, Common Mistakes, "
            "Frequent Issues Requiring Intervention, and Overall Submission Quality Assessment. "
            "Where possible, include approximate counts or percentages derived from the evidence. "
            "Keep it practical and concise for instructor decision-making.\n\n"
            f"Assignment: {assignment_name}\n\n"
            "Analysis to synthesize:\n"
            f"{analysis_text}\n"
        )

        synthesis_response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict but helpful instructional assistant producing clean HTML summaries.",
                },
                {
                    "role": "user",
                    "content": synthesis_prompt,
                },
            ],
        )
        summary_html = self._extract_content(synthesis_response)
        if not summary_html:
            raise ValueError("Cohort synthesis pass returned no content.")
        return summary_html

    def _build_review_prompt(
        self,
        assignment_description,
        student_name,
        artifacts,
        draft_feedback,
        rubric=None,
        extra_instructions=None,
    ):
        artifact_summary = self._artifact_summary(artifacts)
        file_samples = self._read_text_samples(artifacts)
        rubric_block = self._build_rubric_block(rubric)
        rubric_section = f"\n{rubric_block}\n" if rubric_block else ""
        extra_section = (
            f"\nAdditional instructor guidance for this assignment:\n{extra_instructions.strip()}\n"
            if extra_instructions and extra_instructions.strip()
            else ""
        )

        return (
            "You are reviewing and improving an AI-generated draft for an instructor. "
            "Revise the draft so it is specific, evidence-based, and aligned to the assignment and rubric. "
            "Keep the same overall structure, but improve clarity, accuracy, and actionability. "
            "Do not invent code behavior not supported by the submission samples. "
            "Output valid HTML only using simple tags.\n\n"
            f"Student: {student_name}\n\n"
            "Assignment description from Canvas:\n"
            f"{assignment_description[:8000]}\n"
            f"{rubric_section}"
            f"{extra_section}\n"
            "Artifacts available:\n"
            f"{artifact_summary}\n\n"
            "Extracted text samples from submitted files:\n"
            f"{file_samples}\n\n"
            "Current draft feedback to revise:\n"
            f"{draft_feedback}\n"
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

    @staticmethod
    def _extract_score_from_feedback(feedback):
        if not feedback:
            return None

        try:
            raw = str(feedback)
            compact = re.sub(r"\s+", " ", raw).strip()
            plain = re.sub(r"<[^>]+>", " ", compact)
            plain = re.sub(r"\s+", " ", plain).strip()

            patterns = [
                r"proposed\s*score[^0-9]{0,40}(\d+(?:\.\d+)?)",
                r"\btotal\b[^0-9]{0,40}(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?",
            ]

            for target in (compact, plain):
                for pattern in patterns:
                    matches = list(re.finditer(pattern, target, flags=re.IGNORECASE))
                    if not matches:
                        continue

                    match = matches[-1]
                    value = float(match.group(1))
                    if value < 0:
                        return None
                    return value
        except (TypeError, ValueError):
            return None

        return None

    def generate_feedback(
        self,
        assignment_description,
        student_name,
        artifacts,
        rubric=None,
        extra_instructions=None,
        enable_review_pass=False,
        enable_detailed_passes=False,
    ):
        model_name = self.model_name
        sampling_diagnostics = {}
        if enable_detailed_passes:
            feedback, model_name = self._generate_feedback_with_detailed_passes(
                assignment_description=assignment_description,
                student_name=student_name,
                artifacts=artifacts,
                rubric=rubric,
                extra_instructions=extra_instructions,
            )
            sampling_diagnostics = dict(self._last_sampling_diagnostics)
        else:
            prompt = self._build_prompt(
                assignment_description=assignment_description,
                student_name=student_name,
                artifacts=artifacts,
                rubric=rubric,
                extra_instructions=extra_instructions,
            )
            sampling_diagnostics = dict(self._last_sampling_diagnostics)

            feedback, model_name = self._run_generation_pass(
                "You are a strict but constructive instructor assistant. Output valid HTML only using simple formatting tags.",
                prompt,
            )

        if enable_review_pass:
            review_prompt = self._build_review_prompt(
                assignment_description=assignment_description,
                student_name=student_name,
                artifacts=artifacts,
                draft_feedback=feedback,
                rubric=rubric,
                extra_instructions=extra_instructions,
            )
            reviewed_feedback, model_name = self._run_generation_pass(
                "You are a strict but constructive instructor assistant. Revise for quality and output valid HTML only using simple formatting tags.",
                review_prompt,
            )
            if reviewed_feedback:
                feedback = reviewed_feedback

        score = self._extract_score_from_feedback(feedback)

        return AIDraftResult(
            feedback=feedback,
            score=score,
            provider_name=self.provider_name,
            model_name=model_name,
            prompt_diagnostics=sampling_diagnostics,
        )
