from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tanshin_pipeline.config import ANALYSIS_MAX_OUTPUT_TOKENS, output_paths
from tanshin_pipeline.costing import estimate_text_tokens
from tanshin_pipeline.gemini_runtime import ExecutionResult
from tanshin_pipeline.persistence import read_json, write_json
from tanshin_pipeline.pipeline import (
    execute_translation,
    prepare_analysis,
    prepare_translation,
)
from tanshin_pipeline.prompts import (
    TRANSLATION_SYSTEM_PROMPT,
    translation_prompt_template,
)
from tanshin_pipeline.schemas import (
    EnglishTranslation,
    EnglishTranslationPatch,
    JapaneseAnalysis,
    SupportedSpan,
    TranslatedSpanPatch,
)
from tanshin_pipeline.translation_contract import (
    TranslationContractError,
    materialize_english_translation,
)
from tests.helpers import (
    persist_fake_research,
    workspace_temp_directory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _stored_analysis() -> JapaneseAnalysis:
    return JapaneseAnalysis.model_validate(
        read_json(FIXTURES / "fake_analysis_ja.json")
    )


def _stored_translation() -> EnglishTranslation:
    return EnglishTranslation.model_validate(
        read_json(FIXTURES / "fake_translation_en.json")
    )


def _patch_from_translation(
    translation: EnglishTranslation,
) -> EnglishTranslationPatch:
    return EnglishTranslationPatch.model_validate(
        {
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "headline_en": claim.headline_en,
                    "body_en": claim.body_en,
                    "figures": [
                        {
                            "value_id": span.value_id,
                            "claim_surface_en": span.claim_surface_en,
                        }
                        for span in claim.figures
                    ],
                    "dates": [
                        {
                            "value_id": span.value_id,
                            "claim_surface_en": span.claim_surface_en,
                        }
                        for span in claim.dates
                    ],
                    "qualifiers": [
                        {
                            "value_id": span.value_id,
                            "claim_surface_en": span.claim_surface_en,
                        }
                        for span in claim.qualifiers
                    ],
                }
                for claim in translation.claims
            ],
            "model_notes": translation.model_notes,
        }
    )


class TranslationContractTests(unittest.TestCase):
    def test_patch_materialization_restores_immutable_fields_in_source_order(
        self,
    ) -> None:
        analysis = _stored_analysis()
        patch_payload = _patch_from_translation(
            _stored_translation()
        ).model_dump(mode="json")
        patch_payload["claims"].reverse()
        translation = materialize_english_translation(
            analysis,
            EnglishTranslationPatch.model_validate(patch_payload),
        )

        self.assertEqual(translation.schema_version, analysis.schema_version)
        self.assertEqual(translation.identity, analysis.identity)
        self.assertEqual(
            [claim.claim_id for claim in translation.claims],
            [claim.claim_id for claim in analysis.claims],
        )
        for source, translated in zip(analysis.claims, translation.claims):
            self.assertEqual(translated.section, source.section)
            self.assertEqual(translated.order, source.order)
            self.assertEqual(translated.evidence_ids, source.evidence_ids)
            self.assertEqual(translated.statement_type, source.statement_type)
            self.assertEqual(translated.is_inference, source.is_inference)
            self.assertEqual(translated.causal, source.causal)
        self.assertEqual(translation.evidence_translations, [])

    def test_patch_materialization_restores_span_metadata(self) -> None:
        analysis = _stored_analysis().model_copy(deep=True)
        source_claim = analysis.claims[0]
        source_claim.figures = [
            SupportedSpan(
                value_id="c001:figure:01",
                claim_surface_ja="834億円",
                source_surface_ja="834億円",
                evidence_id=source_claim.evidence_ids[0],
            )
        ]
        patch = _patch_from_translation(_stored_translation())
        patch.claims[0].figures = [
            TranslatedSpanPatch(
                value_id="c001:figure:01",
                claim_surface_en="¥83.4 billion",
            )
        ]
        translation = materialize_english_translation(analysis, patch)
        span = translation.claims[0].figures[0]
        self.assertEqual(span.value_id, "c001:figure:01")
        self.assertEqual(span.claim_surface_en, "¥83.4 billion")
        self.assertEqual(span.source_surface_ja, "834億円")
        self.assertEqual(span.evidence_id, source_claim.evidence_ids[0])

    def test_patch_materialization_rejects_missing_and_duplicate_ids(self) -> None:
        analysis = _stored_analysis()
        patch = _patch_from_translation(_stored_translation())
        patch.claims.pop()
        with self.assertRaisesRegex(TranslationContractError, "missing claim IDs"):
            materialize_english_translation(analysis, patch)

        patch = _patch_from_translation(_stored_translation())
        patch.claims.append(patch.claims[0].model_copy(deep=True))
        with self.assertRaisesRegex(
            TranslationContractError,
            "Duplicate translation patch claim IDs",
        ):
            materialize_english_translation(analysis, patch)

    def test_patch_materialization_rejects_missing_span_ids(self) -> None:
        analysis = _stored_analysis().model_copy(deep=True)
        source_claim = analysis.claims[0]
        source_claim.figures = [
            SupportedSpan(
                value_id="c001:figure:01",
                claim_surface_ja="834億円",
                source_surface_ja="834億円",
                evidence_id=source_claim.evidence_ids[0],
            )
        ]
        patch = _patch_from_translation(_stored_translation())
        with self.assertRaisesRegex(
            TranslationContractError,
            "missing c001 figure IDs",
        ):
            materialize_english_translation(analysis, patch)

    def test_materialized_translation_prompt_is_costed_once(self) -> None:
        analysis = _stored_analysis()
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            paths = output_paths(output_root, "1808")
            persist_fake_research(REPOSITORY_ROOT, paths)
            analysis_run = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            write_json(analysis_run.paths.analysis_normalized, analysis)
            translation_run = prepare_translation(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )

        materialized_schema_text = json.dumps(
            translation_run.spec.response_schema,
            ensure_ascii=False,
            sort_keys=True,
        )
        materialized_expected = (
            estimate_text_tokens(translation_run.spec.system_prompt)
            + estimate_text_tokens(translation_run.spec.prompt)
            + estimate_text_tokens(materialized_schema_text)
        )
        self.assertEqual(
            translation_run.cost.translation.estimated_input_tokens,
            materialized_expected,
        )

        future_translation_schema_text = json.dumps(
            translation_run.spec.response_schema,
            ensure_ascii=False,
            sort_keys=True,
        )
        future_expected = (
            ANALYSIS_MAX_OUTPUT_TOKENS
            + estimate_text_tokens(TRANSLATION_SYSTEM_PROMPT)
            + estimate_text_tokens(translation_prompt_template())
            + estimate_text_tokens(future_translation_schema_text)
        )
        self.assertEqual(
            analysis_run.cost.translation.estimated_input_tokens,
            future_expected,
        )

    def test_execute_translation_persists_full_materialized_artifact(self) -> None:
        analysis = _stored_analysis()
        patch_response = _patch_from_translation(_stored_translation())
        result = ExecutionResult(
            structured=patch_response,
            raw_response={"response_id": "translation-patch-response"},
            usage={"prompt_token_count": 100, "candidates_token_count": 20},
            model_version="fake-version",
            response_id="translation-patch-response",
            finish_reason="STOP",
            attempts=1,
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            paths = output_paths(output_root, "1808")
            persist_fake_research(REPOSITORY_ROOT, paths)
            analysis_run = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            write_json(analysis_run.paths.analysis_normalized, analysis)
            prepared = prepare_translation(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            with patch(
                "tanshin_pipeline.pipeline._execute_model_request",
                return_value=result,
            ):
                execute_translation(
                    REPOSITORY_ROOT,
                    "1808",
                    confirmed_request_id=prepared.plan.request_id,
                    output_root=output_root,
                )
            structured = EnglishTranslation.model_validate(
                read_json(prepared.paths.translation_structured)
            )
            response_schema = read_json(prepared.paths.translation_schema)

        self.assertEqual(structured.identity, analysis.identity)
        self.assertEqual(structured.evidence_translations, [])
        self.assertEqual(
            [claim.section for claim in structured.claims],
            [claim.section for claim in analysis.claims],
        )
        self.assertNotIn("identity", response_schema["properties"])
        self.assertNotIn("evidence_translations", response_schema["properties"])


if __name__ == "__main__":
    unittest.main()
