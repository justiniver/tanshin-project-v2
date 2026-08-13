from __future__ import annotations

import json
import unittest
from pathlib import Path

from tanshin_pipeline.config import (
    RESEARCH_MAX_COMMENTARY_OBSERVATIONS,
    RESEARCH_MAX_COMMITMENTS,
    RESEARCH_MAX_DISCLOSURES,
    RESEARCH_MAX_FINANCIAL_OBSERVATIONS,
    RESEARCH_MAX_SOURCE_RECORDS,
)
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    TRANSLATION_SYSTEM_PROMPT,
    build_translation_prompt,
    load_generic_blueprint,
)
from tanshin_pipeline.request_builder import (
    build_analysis_spec,
    build_research_spec,
)
from tanshin_pipeline.schemas import (
    EnglishTranslationPatch,
    JapaneseAnalysis,
    JapaneseResearchDossier,
    JapaneseSynthesisResponse,
)
from tanshin_pipeline.selection import select_filings
from tests.helpers import fake_research_dossier


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class PromptBlueprintTests(unittest.TestCase):
    def test_blueprint_contains_no_company_exemplar_content(self) -> None:
        blueprint = load_generic_blueprint(REPOSITORY_ROOT)
        self.assertEqual(
            blueprint.path.relative_to(REPOSITORY_ROOT).as_posix(),
            "prompt_assets/generic_report_blueprint_ja.md",
        )
        for forbidden in (
            "長谷工",
            "荏原",
            "HASEKO",
            "EBARA",
            "E-Plan",
            "NBj",
            "NS計画",
            "1808",
            "6361",
        ):
            self.assertNotIn(forbidden, blueprint.text)

    def test_all_companies_use_the_same_fact_free_blueprint(self) -> None:
        blueprint_hashes: set[str | None] = set()
        for security_code in ("1808", "3923", "6361"):
            manifest = select_filings(REPOSITORY_ROOT, security_code)
            dossier = fake_research_dossier(
                REPOSITORY_ROOT,
                security_code,
            )
            research_spec = build_research_spec(REPOSITORY_ROOT, manifest)
            spec = build_analysis_spec(REPOSITORY_ROOT, manifest, dossier)
            plan = spec.plan()
            blueprint_hashes.add(plan.style_blueprint_sha256)
            self.assertGreater(len(research_spec.files), 0)
            self.assertEqual(spec.files, ())
            self.assertEqual(
                plan.style_blueprint_path,
                "prompt_assets/generic_report_blueprint_ja.md",
            )
            self.assertIsNone(plan.exemplar_path)
            self.assertIsNone(plan.exemplar_sha256)
            self.assertIn("<report_blueprint>", spec.prompt)
            self.assertNotIn("<EXEMPLAR>", spec.prompt)
            self.assertNotIn(
                "能力基盤は不変だが、収益化は循環。",
                spec.prompt,
            )
            self.assertNotIn(
                "事業間の補完が一貫した業績説明の文法。",
                spec.prompt,
            )
            self.assertIn(
                "about 1,500-2,000",
                spec.prompt,
            )
            self.assertIn(
                "about 350-475 characters",
                spec.prompt,
            )
            self.assertIn(
                "Coverage targets (grounding overrides counts)",
                spec.prompt,
            )
            self.assertIn("company.overview: exactly 1 claim", spec.prompt)
            self.assertIn("trend_period_buckets:", spec.prompt)
            self.assertIn("- early:", spec.prompt)
            self.assertIn("- middle:", spec.prompt)
            self.assertIn("- recent:", spec.prompt)
            self.assertIn("coverage_shortfall:<section>", spec.prompt)
            self.assertIn("<research_dossier>", spec.prompt)
            self.assertIn("<research_metrics>", spec.prompt)
            self.assertIn(
                "before -> transition -> current state",
                spec.prompt,
            )
            self.assertIn("positive for investors", spec.prompt)
            self.assertIn("commitment-versus-outcome", spec.prompt)
            self.assertIn(
                "earlier commitment -> later action -> later result",
                spec.prompt,
            )
            self.assertIn("management.strategy: exactly 1", spec.prompt)
            self.assertIn("management.forecast_discipline: exactly 1", spec.prompt)
            self.assertNotIn("evidence", spec.response_schema["required"])
            self.assertLess(
                spec.prompt.index("<document_manifest>"),
                spec.prompt.index("<research_dossier>"),
            )
            self.assertLess(
                spec.prompt.index("</report_blueprint>"),
                spec.prompt.index("<analysis_task>"),
            )
            self.assertTrue(spec.prompt.rstrip().endswith("</analysis_task>"))
        self.assertEqual(len(blueprint_hashes), 1)
        self.assertNotIn(None, blueprint_hashes)

    def test_blueprint_demonstrates_analysis_without_company_facts(self) -> None:
        blueprint = load_generic_blueprint(REPOSITORY_ROOT)
        self.assertIn("## 分析関係の見本", blueprint.text)
        self.assertIn("## 企業概要", blueprint.text)
        self.assertIn("どのような仕組みで収益を得るか", blueprint.text)
        self.assertIn("能力の継続と利益の安定を分けて", blueprint.text)
        self.assertIn("改善は何の証拠", blueprint.text)
        self.assertNotIn("165～215", blueprint.text)
        self.assertIn("同じ損益結果を", blueprint.text)
        self.assertIn("単年度の", blueprint.text)
        self.assertIn("経営成績に関する説明", blueprint.text)
        self.assertIn("経営一貫性スコア", blueprint.text)
        self.assertIn("宣伝的・断定的", blueprint.text)
        self.assertIn("約束、後の実行、結果、現在の意味", blueprint.text)
        self.assertIn("既存中核、主力候補、育成対象", blueprint.text)
        self.assertIn("成長投資、買収・売却、財務運営", blueprint.text)
        self.assertIn("経済的成果が証明されたことを区別", blueprint.text)

    def test_critical_rules_are_in_system_prompts(self) -> None:
        self.assertIn("# Non-negotiable grounding rules", RESEARCH_SYSTEM_PROMPT)
        self.assertIn("outside knowledge", RESEARCH_SYSTEM_PROMPT)
        self.assertIn("Return only one JSON object", RESEARCH_SYSTEM_PROMPT)
        self.assertIn("# Source boundary", ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("outside knowledge", ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("Return only one JSON object", ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("# Non-negotiable invariants", TRANSLATION_SYSTEM_PROMPT)
        self.assertIn("Do not perform new analysis", TRANSLATION_SYSTEM_PROMPT)
        self.assertIn("Return only one JSON object", TRANSLATION_SYSTEM_PROMPT)

    def test_translation_places_long_context_before_task(self) -> None:
        analysis = JapaneseAnalysis.model_validate(
            read_json(FIXTURES / "fake_analysis_ja.json")
        )
        prompt = build_translation_prompt(analysis)
        self.assertLess(
            prompt.index("<translation_input>"),
            prompt.index("<translation_task>"),
        )
        self.assertTrue(prompt.rstrip().endswith("</translation_task>"))
        payload_text = prompt.split("<translation_input>\n", 1)[1].split(
            "\n</translation_input>",
            1,
        )[0]
        payload = json.loads(payload_text)
        self.assertEqual(set(payload), {"identity_context", "claims"})
        self.assertEqual(
            set(payload["claims"][0]),
            {
                "claim_id",
                "section",
                "headline_ja",
                "body_ja",
                "figures",
                "dates",
                "qualifiers",
            },
        )
        self.assertNotIn("evidence", payload)
        self.assertNotIn("management_consistency", payload)
        self.assertNotIn("model_notes", payload)
        self.assertNotIn("evidence_ids", payload["claims"][0])
        self.assertNotIn("statement_type", payload["claims"][0])
        self.assertNotIn("is_inference", payload["claims"][0])
        self.assertNotIn("causal", payload["claims"][0])
        self.assertIn("do not editorialize, summarize, or add conclusions", prompt)
        self.assertIn("render `834億円` as `¥83.4 billion`", prompt)
        self.assertIn("amounts of ¥1 billion or more", prompt)
        self.assertIn("do not mix scales for comparable", prompt)
        self.assertIn("never perform FX conversion", prompt)
        self.assertIn("Never translate names by meaning", prompt)
        self.assertIn("Python restores those immutable fields", prompt)
        self.assertIn("Translate company.overview", prompt)

    def test_model_facing_schema_fields_have_descriptions(self) -> None:
        analysis_schema = JapaneseSynthesisResponse.model_json_schema()
        research_schema = JapaneseResearchDossier.model_json_schema()
        translation_schema = EnglishTranslationPatch.model_json_schema()
        self.assertIn("source_records", research_schema["properties"])
        self.assertIn("filing_coverage", research_schema["properties"])
        self.assertIn("financial_observations", research_schema["properties"])
        self.assertIn("commentary_observations", research_schema["properties"])
        self.assertIn("disclosures", research_schema["properties"])
        self.assertNotIn("evidence", research_schema["properties"])
        self.assertNotIn("management_consistency", research_schema["properties"])
        self.assertIn(
            "coverage_gaps",
            research_schema["$defs"]["ResearchFilingCoverage"]["properties"],
        )
        self.assertIn(
            "description",
            analysis_schema["$defs"]["SynthesisAnalysisClaim"]["properties"][
                "body_ja"
            ],
        )
        self.assertIn(
            "description",
            research_schema["$defs"]["ResearchSourceRecord"]["properties"][
                "pdf_page"
            ],
        )
        self.assertIn(
            "description",
            analysis_schema["$defs"]["SynthesisAnalysisClaim"]["properties"][
                "source_record_ids"
            ],
        )
        self.assertIn(
            "description",
            translation_schema["$defs"]["TranslatedClaimPatch"]["properties"][
                "body_en"
            ],
        )
        patch_claim = translation_schema["$defs"]["TranslatedClaimPatch"][
            "properties"
        ]
        self.assertNotIn("section", patch_claim)
        self.assertNotIn("order", patch_claim)
        self.assertNotIn("evidence_ids", patch_claim)
        self.assertNotIn("statement_type", patch_claim)
        self.assertNotIn("source_surface_ja", json.dumps(translation_schema))
        self.assertNotIn("EvidenceTranslation", translation_schema["$defs"])

    def test_research_request_has_compact_native_output_limits(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "1808")
        spec = build_research_spec(REPOSITORY_ROOT, manifest)
        normalized_prompt = " ".join(spec.prompt.split())
        provider_properties = spec.response_schema["properties"]
        self.assertNotIn("maxItems", provider_properties["source_records"])
        self.assertNotIn(
            "maxItems",
            provider_properties["financial_observations"],
        )
        self.assertIn(
            f"at most {RESEARCH_MAX_SOURCE_RECORDS} source records",
            spec.prompt,
        )
        for ceiling in (
            RESEARCH_MAX_FINANCIAL_OBSERVATIONS,
            RESEARCH_MAX_COMMENTARY_OBSERVATIONS,
            RESEARCH_MAX_DISCLOSURES,
            RESEARCH_MAX_COMMITMENTS,
        ):
            self.assertIn(str(ceiling), spec.prompt)
        self.assertIn("comparable annual anchor", spec.prompt)
        self.assertIn(
            "current-year actual and the next",
            spec.prompt,
        )
        self.assertIn("counts are ceilings, not quotas", normalized_prompt)
        self.assertIn("Do not return business-driver rankings", spec.prompt)
        self.assertIn("management-consistency ratings", RESEARCH_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
