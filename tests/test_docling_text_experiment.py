from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.docling_text_pipeline.common import (
    document_block,
    experiment_artifact_paths,
    parse_page_blocks,
    parsed_json_filename,
    parsed_markdown_filename,
    sha256_bytes,
    sha256_json,
    sha256_text,
    write_json,
    write_text,
)
from experiments.docling_text_pipeline.extract_worker import (
    DoclingExtractionError,
    _conversion_errors,
    _conversion_status_name,
    extract_selected_filings,
)
from experiments.docling_text_pipeline.pipeline import (
    _default_extractor_runner,
    _extractor_environment,
    build_text_analysis_spec,
    prepare_text_analysis,
)
from tanshin_pipeline.config import OPENAI_SOL_MODEL
from tanshin_pipeline.gemini_runtime import execute_request
from tanshin_pipeline.openai_runtime import (
    execute_request as execute_openai_request,
)
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.pipeline import PipelineConfigurationError
from tanshin_pipeline.schemas import JapaneseAnalysis
from tanshin_pipeline.selection import select_filings

from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _Dumpable:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class _FakeResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = json.dumps(parsed, ensure_ascii=False)
        self.candidates = [
            type("Candidate", (), {"finish_reason": "STOP"})()
        ]
        self.usage_metadata = _Dumpable(
            {"prompt_token_count": 100, "candidates_token_count": 20}
        )
        self.model_version = "fake-version"
        self.response_id = "fake-id"

    def model_dump(self, **_kwargs):
        return {"response_id": self.response_id}


class _FakeModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)
        self.closed = False

    def close(self):
        self.closed = True


class _FakeOpenAIResponse:
    def __init__(self, parsed):
        self.output_parsed = parsed
        self.status = "completed"
        self.incomplete_details = None
        self.usage = _Dumpable(
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "output_tokens_details": {"reasoning_tokens": 5},
            }
        )
        self.model = OPENAI_SOL_MODEL
        self.id = "fake-openai-id"

    def model_dump(self, **_kwargs):
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
        }


class _FakeOpenAIResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeOpenAIClient:
    def __init__(self, response):
        self.responses = _FakeOpenAIResponses(response)
        self.closed = False

    def close(self):
        self.closed = True


class _FakeConversionStatus:
    value = "partial_success"


class _FakeConversionResult:
    status = _FakeConversionStatus()
    errors = ["page 3 failed"]


def _fake_extractor(
    repository_root: Path,
    selection_manifest_path: Path,
    output_root: Path,
    _docling_python: Path,
    _models_dir: Path,
    _force_reparse: bool,
) -> None:
    selection = json.loads(
        selection_manifest_path.read_text(encoding="utf-8")
    )
    paths = experiment_artifact_paths(
        output_root,
        selection["security_code"],
    )
    paths.parsed_sources_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "docling_version": "fake-docling",
        "options": {"do_ocr": False, "page_markers": True},
    }
    config_hash = sha256_json(config)
    entries = []
    blocks = []
    for selected in selection["selected_files"]:
        filename = selected["filename"]
        pages = [
            (
                f"# {filename} page {page}\n\n"
                f"経営成績と業績予想のテスト本文 {page}。"
            )
            for page in range(1, selected["page_count"] + 1)
        ]
        rendered = document_block(
            filename=filename,
            page_count=selected["page_count"],
            page_markdown=pages,
        )
        markdown_path = (
            paths.parsed_sources_dir
            / parsed_markdown_filename(filename)
        )
        json_path = (
            paths.parsed_sources_dir / parsed_json_filename(filename)
        )
        write_text(markdown_path, rendered)
        write_json(json_path, {"fake": True, "pages": len(pages)})
        entries.append(
            {
                "filename": filename,
                "source_relative_path": selected["relative_path"],
                "source_sha256": selected["sha256"],
                "expected_pdf_pages": selected["page_count"],
                "extracted_pages": selected["page_count"],
                "markdown_relative_path": markdown_path.relative_to(
                    repository_root
                ).as_posix(),
                "json_relative_path": json_path.relative_to(
                    repository_root
                ).as_posix(),
                "markdown_sha256": sha256_text(rendered),
                "json_sha256": sha256_bytes(json_path.read_bytes()),
                "config_sha256": config_hash,
                "conversion_status": "SUCCESS",
                "conversion_errors": [],
                "cache_status": "fake",
                "empty_pages": [],
                "page_entries": [
                    {
                        "page": page,
                        "characters": len(text),
                        "non_whitespace_characters": len(
                            "".join(text.split())
                        ),
                        "replacement_characters": 0,
                        "sha256": sha256_text(text),
                    }
                    for page, text in enumerate(pages, start=1)
                ],
            }
        )
        blocks.append(rendered)
    corpus = "\n\n".join(blocks) + "\n"
    write_text(paths.text_corpus, corpus)
    write_json(
        paths.extraction_manifest,
        {
            "schema_version": "fake",
            "security_code": selection["security_code"],
            "source_manifest_id": selection["manifest_id"],
            "docling_version": "fake-docling",
            "options": config["options"],
            "config_sha256": config_hash,
            "models_directory": "fake",
            "corpus_relative_path": paths.text_corpus.relative_to(
                repository_root
            ).as_posix(),
            "corpus_sha256": sha256_text(corpus),
            "total_characters": len(corpus),
            "total_non_whitespace_characters": len(
                "".join(corpus.split())
            ),
            "total_duration_seconds": 0,
            "files": entries,
        },
    )


class DoclingTextExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = select_filings(REPOSITORY_ROOT, "1808")

    def test_page_markers_round_trip(self) -> None:
        rendered = document_block(
            filename="example.pdf",
            page_count=2,
            page_markdown=["一頁目です。", "二頁目です。"],
        )
        pages = parse_page_blocks(rendered)
        self.assertEqual(
            pages,
            {1: "一頁目です。", 2: "二頁目です。"},
        )
        self.assertIn("<source_filename>example.pdf</source_filename>", rendered)

    def test_text_spec_has_no_file_attachments(self) -> None:
        extraction = {
            "corpus_sha256": sha256_text("corpus-a"),
            "config_sha256": "config-a",
        }
        spec = build_text_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            '<PHYSICAL_PAGE number="1">corpus-a</PHYSICAL_PAGE>',
            extraction,
        )
        self.assertEqual(spec.files, ())
        self.assertEqual(
            spec.request_options["source_representation"],
            "docling_markdown",
        )
        self.assertIn("<PHYSICAL_PAGE", spec.context_prompt or "")
        self.assertNotIn("application/pdf", spec.prompt)
        self.assertIn("page-preserving Docling", spec.system_prompt)
        self.assertIn("Markdown derived locally", spec.system_prompt)

    def test_request_id_changes_when_corpus_changes(self) -> None:
        first = build_text_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            '<PHYSICAL_PAGE number="1">A</PHYSICAL_PAGE>',
            {
                "corpus_sha256": sha256_text("A"),
                "config_sha256": "same-config",
            },
        )
        second = build_text_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            '<PHYSICAL_PAGE number="1">B</PHYSICAL_PAGE>',
            {
                "corpus_sha256": sha256_text("B"),
                "config_sha256": "same-config",
            },
        )
        self.assertNotEqual(
            first.plan().request_id,
            second.plan().request_id,
        )

    def test_gemini_runtime_sends_two_text_parts_and_no_pdf_bytes(self) -> None:
        spec = build_text_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            '<PHYSICAL_PAGE number="1">テスト</PHYSICAL_PAGE>',
            {
                "corpus_sha256": sha256_text("test"),
                "config_sha256": "fake",
            },
        )
        payload = read_json(FIXTURES / "fake_analysis_ja.json")
        fake = _FakeClient(_FakeResponse(payload))
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            result = execute_request(
                REPOSITORY_ROOT,
                spec,
                confirmed_request_id=spec.plan().request_id,
                client_factory=lambda: fake,
                configured_model_getter=lambda: spec.model,
            )
        self.assertIsInstance(result.structured, JapaneseAnalysis)
        self.assertEqual(len(fake.models.calls), 1)
        parts = fake.models.calls[0]["contents"][0].parts
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.text for part in parts))
        self.assertIn("<experimental_source_representation>", parts[0].text)
        self.assertIn("<PHYSICAL_PAGE", parts[0].text)
        self.assertTrue(fake.closed)

    def test_openai_runtime_sends_only_text_and_no_input_file(self) -> None:
        spec = build_text_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            '<PHYSICAL_PAGE number="1">テスト</PHYSICAL_PAGE>',
            {
                "corpus_sha256": sha256_text("test"),
                "config_sha256": "fake",
            },
            model_profile="sol",
        )
        payload = read_json(FIXTURES / "fake_analysis_ja.json")
        parsed = JapaneseAnalysis.model_validate(payload)
        fake = _FakeOpenAIClient(_FakeOpenAIResponse(parsed))
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            result = execute_openai_request(
                REPOSITORY_ROOT,
                spec,
                confirmed_request_id=spec.plan().request_id,
                client_factory=lambda: fake,
                configured_model_getter=lambda: OPENAI_SOL_MODEL,
            )
        self.assertIsInstance(result.structured, JapaneseAnalysis)
        content = fake.responses.calls[0]["input"][0]["content"]
        self.assertEqual(
            [item["type"] for item in content],
            ["input_text", "input_text"],
        )
        self.assertNotIn("pdf_detail", spec.request_options)
        self.assertTrue(fake.closed)

    def test_extractor_environment_excludes_provider_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "must-not-be-inherited",
                "GEMINI_API_KEY2": "must-not-be-inherited",
                "OPENAI_API_KEY": "must-not-be-inherited",
                "SYSTEMROOT": r"C:\Windows",
            },
            clear=False,
        ):
            environment = _extractor_environment()
        self.assertNotIn("GEMINI_API_KEY", environment)
        self.assertNotIn("GEMINI_API_KEY2", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")

    def test_partial_docling_status_is_not_treated_as_success(self) -> None:
        result = _FakeConversionResult()
        self.assertEqual(
            _conversion_status_name(result),
            "PARTIAL_SUCCESS",
        )
        self.assertEqual(_conversion_errors(result), ["page 3 failed"])

    def test_changed_pdf_hash_blocks_before_docling_conversion(self) -> None:
        experiments_root = REPOSITORY_ROOT / "output" / "experiments"
        with workspace_temp_directory(experiments_root) as temporary:
            output_root = temporary / "docling_output"
            models_dir = temporary / "models"
            models_dir.mkdir()
            (models_dir / "placeholder.bin").write_bytes(b"offline-model")
            selection = self.manifest.model_dump(mode="json")
            selection["selected_files"] = [selection["selected_files"][0]]
            selection["selected_files"][0]["sha256"] = "0" * 64
            selection["manifest_id"] = "changed-source-test"
            manifest_path = temporary / "selection.json"
            write_json(manifest_path, selection)
            with self.assertRaisesRegex(
                DoclingExtractionError,
                "source PDF changed",
            ):
                extract_selected_filings(
                    repository_root=REPOSITORY_ROOT,
                    selection_manifest_path=manifest_path,
                    output_root=output_root,
                    models_dir=models_dir,
                    force_reparse=False,
                )

    def test_prepare_is_offline_and_writes_only_to_experimental_root(self) -> None:
        experiments_root = REPOSITORY_ROOT / "output" / "experiments"
        with workspace_temp_directory(experiments_root) as temporary:
            output_root = temporary / "docling_output"
            with patch.dict(
                os.environ,
                {
                    "TANSHIN_OFFLINE_ONLY": "1",
                    "TANSHIN_TESTING": "1",
                },
                clear=False,
            ):
                prepared = prepare_text_analysis(
                    REPOSITORY_ROOT,
                    "1808",
                    output_root=output_root,
                    docling_python=temporary / "unused-python.exe",
                    models_dir=temporary / "unused-models",
                    extractor_runner=_fake_extractor,
                )
            self.assertEqual(prepared.spec.files, ())
            self.assertTrue(prepared.paths.analysis_request_plan.is_file())
            self.assertTrue(prepared.paths.cost.is_file())
            self.assertTrue(
                experiment_artifact_paths(
                    output_root,
                    "1808",
                ).input_size_comparison.is_file()
            )
            self.assertTrue(
                str(prepared.paths.output_dir).startswith(str(output_root))
            )
            self.assertEqual(
                prepared.cost.pdf_tokens_per_page,
                0,
            )

    def test_production_output_root_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            PipelineConfigurationError,
            "output.*experiments",
        ):
            prepare_text_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=REPOSITORY_ROOT / "output",
                extractor_runner=_fake_extractor,
            )

    def test_missing_docling_environment_has_clear_setup_message(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temporary:
            with self.assertRaisesRegex(
                PipelineConfigurationError,
                "setup_docling_experiment",
            ):
                _default_extractor_runner(
                    REPOSITORY_ROOT,
                    temporary / "selection.json",
                    temporary / "output",
                    temporary / "missing-python.exe",
                    temporary / "models",
                    False,
                )


if __name__ == "__main__":
    unittest.main()
