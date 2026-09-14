#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for classify.py's category + AV-relevance logic.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_classify.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classify as cl


class TestClassifyRelevance(unittest.TestCase):
    def test_title_mention_is_core(self):
        self.assertEqual(
            cl.classify_relevance("PlanNet: Planning for Autonomous Driving", "A method for planning."),
            "AV",
        )

    def test_driving_as_a_standalone_title_word_is_core(self):
        # DriveLM: the compound "DriveLM" itself doesn't match any
        # AV_RELEVANCE_TERMS phrase, but "Driving" appears standalone
        # elsewhere in the title -- this must not fall through to "non-AV".
        self.assertEqual(
            cl.classify_relevance(
                "DriveLM: Driving with Graph Visual Question Answering",
                "A framework connecting perception to planning.",
            ),
            "AV",
        )

    def test_data_driven_does_not_false_positive_on_driven(self):
        # "data-driven" / "goal-driven" are extremely common ML title
        # phrases unrelated to vehicles -- must not match "drive"/"driving".
        self.assertEqual(
            cl.classify_relevance(
                "A Data-Driven Approach to Image Classification",
                "We classify images using a data-driven neural network.",
            ),
            "non-AV",
        )

    def test_single_abstract_mention_is_core(self):
        # A general-purpose paper that only lists autonomous driving as one
        # of several downstream applications still counts as core -- there is
        # no partial credit, a paper is either AV-relevant or it isn't.
        self.assertEqual(
            cl.classify_relevance(
                "Depth Anything: A Foundation Model",
                "Our model is applicable to robotics, autonomous driving, and augmented reality.",
            ),
            "AV",
        )

    def test_no_av_terms_is_adjacent(self):
        self.assertEqual(
            cl.classify_relevance("Fast Image Classification", "We classify images of cats and dogs."),
            "non-AV",
        )

    def test_missing_abstract_does_not_crash(self):
        self.assertEqual(cl.classify_relevance("Some Paper", None), "non-AV")

    def test_traffic_light_and_sign_count_as_av_relevant(self):
        # user-flagged real miss: "A VT-HMM-Based Framework for Countdown
        # Timer Traffic Light State Estimation" had no abstract and no other
        # AV-specific phrase in its title, so it fell through to "non-AV"
        # despite being unambiguously about real-world driving
        # infrastructure.
        self.assertEqual(
            cl.classify_relevance("Countdown Timer Traffic Light State Estimation", None),
            "AV",
        )
        self.assertEqual(
            cl.classify_relevance("Robust Traffic Sign Recognition in Adverse Weather", None),
            "AV",
        )

    def test_dataset_name_alone_counts_as_av_relevant(self):
        self.assertEqual(
            cl.classify_relevance("A New Benchmark", "We evaluate on nuScenes and compare to prior work."),
            "AV",
        )

    def test_llm_core_promotes_a_keyword_adjacent_paper(self):
        self.assertEqual(
            cl.classify_relevance(
                "Fast Image Classification", "We classify images of cats and dogs.", llm_says_av=True,
            ),
            "AV",
        )

    def test_llm_adjacent_does_not_promote(self):
        self.assertEqual(
            cl.classify_relevance(
                "Fast Image Classification", "We classify images of cats and dogs.", llm_says_av=False,
            ),
            "non-AV",
        )

    # -- Ground-truth regression cases (real papers, user-confirmed) --------

    def test_mechanical_chassis_control_paper_excluded(self):
        # Real paper, confirmed by the user: was wrongly "AV" purely
        # because the title-strength check matched "Drive" inside
        # "Distributed Drive" (a drivetrain configuration, not a person
        # driving). Scope decision: mechanical/hardware vehicle engineering
        # is out of scope, even when "vehicle"/"drive" appears in the title.
        self.assertEqual(
            cl.classify_relevance(
                "Handling and Stability Integrated Control of AFS and DYC for Distributed Drive Electric Vehicle",
                "This paper proposes a chassis control strategy combining active front steering (AFS) and "
                "direct yaw moment control (DYC) for a distributed drive electric vehicle to improve handling "
                "and stability.",
            ),
            "non-AV",
        )

    def test_learning_based_chassis_control_still_included(self):
        # The exclusion above must not blanket-exclude every chassis/vehicle-
        # dynamics paper -- one that applies a learning-based method to the
        # same hardware problem is still in scope (broad interpretation of
        # "learning-based approaches", per the user's own framing).
        self.assertEqual(
            cl.classify_relevance(
                "Deep Reinforcement Learning for Active Front Steering Control",
                "We propose a deep reinforcement learning policy for active front steering (AFS) and direct "
                "yaw moment control (DYC) of a distributed drive electric vehicle for autonomous driving.",
            ),
            "AV",
        )

    # -- Expanded synonym vocabulary (fallback path; these had no abstract on
    #    the real data and their titles matched nothing before) ------------
    def test_automated_and_connected_vehicle_synonyms_are_core(self):
        for title in (
            "Decentralized Cooperative Planning for Automated Vehicles",
            "Cooperative Adaptive Cruise Control of Connected and Automated Vehicles",
            "Deep Reinforcement Learning Based Platooning Control for Fuel Optimization",
            "Identification and Classification of Car-Following Behavior",
            "A Nonlinear MPC Strategy for Autonomous Racing of Scale Vehicles",
        ):
            self.assertEqual(cl.classify_relevance(title, None), "AV", title)

    def test_radar_perception_vocabulary_is_core(self):
        # User-flagged via Andras Palffy's papers: radar-perception papers
        # (a signature AV subfield) fell through to "non-AV" whenever the
        # abstract didn't also say "autonomous driving".
        for title, abstract in (
            ("A Deep Automotive Radar Detector Using the RaDelft Dataset",
             "A data-driven approach using high-resolution automotive radar."),
            ("4D-RaDiff: Latent Diffusion for 4D Radar Point Cloud Generation", None),
            ("Ground-Aware Automotive Radar Odometry", None),
            ("CLRNet: Targetless Extrinsic Calibration for Camera, Lidar and 4D Radar", None),
            ("Occlusion Aware Sensor Fusion for Early Crossing Pedestrian Detection", None),
            ("Pedestrian Crossing Intention Prediction Using Multimodal Fusion Network", None),
        ):
            self.assertEqual(cl.classify_relevance(title, abstract), "AV", title)

    def test_aerial_radar_paper_still_held_out_by_off_scope_guard(self):
        # "4d radar" now fires, but the off-scope title guard runs first.
        self.assertEqual(
            cl.classify_relevance(
                "Robust 4D Radar-Aided Inertial Navigation for Aerial Vehicles", None),
            "non-AV",
        )

    def test_title_only_terms_fire_from_the_title(self):
        # A title-only phrase in the title is enough on its own.
        self.assertEqual(
            cl.classify_relevance("Perception Stack for an Intelligent Vehicle", None), "AV")
        # ...and the same phrase absent everywhere leaves a generic paper adjacent.
        self.assertEqual(
            cl.classify_relevance(
                "A General Graph Neural Network for Node Classification",
                "We evaluate on standard node-classification benchmarks.",
            ),
            "non-AV",
        )

    # -- Off-scope (non-road-vehicle) title guard ------------------------
    def test_non_road_vehicle_platforms_excluded(self):
        for title in (
            "Autonomous Navigation of Unmanned Aerial Vehicles in GPS-Denied Environments",
            "Self-Driving Quadrotor: Vision-Based Autonomous Flight",
            "Autonomous Underwater Vehicle Path Planning with Reinforcement Learning",
            "Stanford Doggo: An Open-Source Quasi-Direct-Drive Quadruped",
            "Learning Dexterous In-Hand Manipulation with a Robotic Gripper",
        ):
            self.assertEqual(cl.classify_relevance(title, None), "non-AV", title)

    def test_off_scope_guard_beats_llm_and_keywords(self):
        # "autonomous ... vehicle" phrase present, LLM says core -- still
        # adjacent, because the title is about a non-road platform.
        self.assertEqual(
            cl.classify_relevance(
                "An Autonomous Aerial Vehicle for Autonomous Driving Dataset Collection",
                "We use an autonomous vehicle and the KITTI dataset.", llm_says_av=True,
            ),
            "non-AV",
        )


class TestTrainedRelevanceModel(unittest.TestCase):
    """classify_relevance uses data/relevance_model.json when present. Build a
    tiny in-memory model and check the wiring (scoring, threshold, the LLM
    OR-promotion, and that the hard pre-filters still win)."""

    def setUp(self):
        import re
        self._saved = cl.RELEVANCE_MODEL
        vocab = ["autonomous driving", "vehicle", "segmentation"]
        cl.RELEVANCE_MODEL = {
            "vocab": vocab,
            "title_coef": {"autonomous driving": 5.0, "vehicle": 0.5, "segmentation": -1.0},
            "abstract_coef": {"autonomous driving": 2.0, "vehicle": 0.1, "segmentation": -0.5},
            "title_strong_coef": 3.0,
            "intercept": -1.0,
            "threshold": 2.0,
            "_patterns": [(t, re.compile(r"\b" + re.escape(t) + r"s?\b", re.I)) for t in vocab],
        }

    def tearDown(self):
        cl.RELEVANCE_MODEL = self._saved

    def test_score_above_threshold_is_core(self):
        # -1.0 + 5.0 (title "autonomous driving") = 4.0 >= 2.0
        self.assertEqual(cl.classify_relevance("Planning for Autonomous Driving", None), "AV")

    def test_score_below_threshold_is_adjacent(self):
        # -1.0 + 0.5 (title "vehicle") = -0.5 < 2.0, no LLM
        self.assertEqual(cl.classify_relevance("A Vehicle Detector", None), "non-AV")

    def test_llm_core_promotes_below_threshold(self):
        self.assertEqual(
            cl.classify_relevance("A Vehicle Detector", None, llm_says_av=True), "AV")

    def test_negative_weight_keeps_generic_paper_adjacent(self):
        # -1.0 + 0.5 (vehicle) - 1.0 (segmentation) = -1.5
        self.assertEqual(
            cl.classify_relevance("Vehicle Part Segmentation", "generic segmentation"), "non-AV")

    def test_off_scope_guard_still_wins_over_model(self):
        self.assertEqual(
            cl.classify_relevance("Autonomous Driving for a Quadrotor UAV", None), "non-AV")

    def test_mechanical_guard_still_wins_over_model(self):
        self.assertEqual(
            cl.classify_relevance(
                "Yaw Moment Control for Autonomous Driving",
                "A classical anti-lock braking system and torque vectoring design.",
            ),
            "non-AV",
        )


class TestKnownDatasetTitleOverride(unittest.TestCase):
    CATEGORIES = [
        # Deliberately generic/broad keywords a real dataset paper's own
        # abstract would otherwise out-score "dataset-benchmark-paper" on --
        # confirmed on real data (nuScenes/Argoverse/Waymo Open Dataset were
        # landing in "tracking"/"object-detection" before this override).
        {"id": "tracking", "keywords": ["tracking", "detection"]},
        {"id": "dataset-benchmark-paper", "keywords": ["new dataset"]},
    ]

    def test_known_dataset_title_forced_to_datasets_category(self):
        category, relevance = cl.classify_paper(
            "nuScenes: A Multimodal Dataset for Autonomous Driving",
            "Our dataset supports detection and tracking. Tracking, tracking, detection, detection, detection.",
            self.CATEGORIES,
            known_dataset_titles={cl.normalize_title("nuScenes: A Multimodal Dataset for Autonomous Driving")},
        )
        self.assertEqual(category, "dataset-benchmark-paper")
        self.assertEqual(relevance, "AV")

    def test_unlisted_dataset_paper_is_still_recognized_by_its_title(self):
        # The known-title list is a floor, not the whole rule: a paper whose
        # own title presents a dataset is categorized as one even when it
        # isn't on the list. This used to fall through to keyword scoring,
        # which is why the category held 31 papers out of 25k while 755 core
        # papers said "dataset"/"benchmark" in their titles -- see
        # presents_dataset() for the full reasoning.
        category, _ = cl.classify_paper(
            "Some Other Dataset Paper", "We introduce detection and tracking benchmarks. Tracking tracking.",
            self.CATEGORIES, known_dataset_titles=frozenset(),
        )
        self.assertEqual(category, "dataset-benchmark-paper")

    def test_paper_that_merely_uses_a_dataset_is_scored_normally(self):
        # The other side of that rule: "on the X dataset" describes what the
        # paper was evaluated on, not what it contributes, so it falls
        # through to ordinary keyword scoring.
        category, _ = cl.classify_paper(
            "Multi-Object Tracking on the KITTI Dataset",
            "We improve detection and tracking. Tracking tracking.",
            self.CATEGORIES, known_dataset_titles=frozenset(),
        )
        self.assertEqual(category, "tracking")

    def test_survey_of_datasets_is_not_a_dataset_paper(self):
        # Not a dataset paper (presents_dataset()'s own review/survey
        # exclusion) -- but it IS a review, which the survey-review-paper
        # gate (checked after this one) now claims instead of falling
        # through to keyword scoring. See TestIsSurveyOrReviewPaper below
        # for that gate's own tests.
        category, _ = cl.classify_paper(
            "Datasets for Lane Detection in Autonomous Driving: A Comprehensive Review",
            "We survey detection and tracking datasets. Tracking tracking.",
            self.CATEGORIES, known_dataset_titles=frozenset(),
        )
        self.assertEqual(category, "survey-review-paper")

    def test_method_paper_deriving_a_dataset_is_not_categorized_as_dataset(self):
        # Real case, user-flagged: "Sparsity Invariant CNNs" derives a depth
        # dataset from KITTI purely to evaluate its own sparse-convolution
        # layer -- the abstract's "novel dataset from the KITTI benchmark"
        # phrasing shouldn't outrank the paper's actual subject just because
        # the title itself never claims to be a dataset paper.
        categories = [
            {"id": "depth-3d-geometry", "keywords": ["sparse convolution", "depth upsampling"]},
            {"id": "dataset-benchmark-paper", "keywords": ["novel dataset"]},
        ]
        category, _ = cl.classify_paper(
            "Sparsity Invariant CNNs",
            "We propose a sparse convolution layer for depth upsampling. We derive a novel dataset from the "
            "KITTI benchmark for evaluation.",
            categories, known_dataset_titles=frozenset(),
        )
        self.assertEqual(category, "depth-3d-geometry")

    def test_dataset_paper_with_dataset_in_title_still_categorized_as_dataset(self):
        # The title-gate must not block genuine (but unlisted) dataset
        # papers -- only ones where the title gives no such signal.
        categories = [{"id": "dataset-benchmark-paper", "keywords": ["novel dataset"]}]
        category, _ = cl.classify_paper(
            "SomeNew Dataset for Depth Estimation",
            "We introduce a novel dataset for depth estimation research.",
            categories, known_dataset_titles=frozenset(),
        )
        self.assertEqual(category, "dataset-benchmark-paper")


class TestIsSurveyOrReviewPaper(unittest.TestCase):
    # Pure gate-detection tests, no categories/keyword scoring involved --
    # see TestClassifyPaper.test_survey_gate_* below for how it interacts
    # with the dataset/radar gates and the misc/uncategorized fallback.
    def test_survey_in_title_detected(self):
        self.assertTrue(cl.is_survey_or_review_paper("A Survey of Motion Planning for Autonomous Vehicles"))

    def test_review_in_title_detected(self):
        self.assertTrue(cl.is_survey_or_review_paper("Review of exteroceptive sensors for autonomous driving"))

    def test_no_survey_or_review_word_not_detected(self):
        self.assertFalse(cl.is_survey_or_review_paper("PointPillars: Fast Encoders for Object Detection"))

    def test_online_survey_is_a_questionnaire_not_a_review(self):
        # Real corpus case: an empirical user study, not a literature
        # review -- of 541 AV papers matching \bsurvey\b|\breview\b in the
        # title, this and the "activity survey" case below were the only
        # two that turned out to mean the other sense of "survey".
        self.assertFalse(cl.is_survey_or_review_paper(
            "What are Social Norms for Low-speed Autonomous Vehicle Navigation in Crowded Environments? "
            "An Online Survey"))

    def test_activity_survey_is_a_questionnaire_not_a_review(self):
        self.assertFalse(cl.is_survey_or_review_paper(
            "Next-generation freight vehicle surveys: Supplementing truck GPS tracking with a driver "
            "activity survey"))


class TestLoadLlmCategoryLabels(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.labels_file = Path(self.tmpdir.name) / "category_labels_llm.json"
        self._orig_file = cl.CATEGORY_LABELS_LLM_FILE
        cl.CATEGORY_LABELS_LLM_FILE = self.labels_file
        self.addCleanup(setattr, cl, "CATEGORY_LABELS_LLM_FILE", self._orig_file)

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(cl.load_llm_category_labels(), {})

    def test_null_category_entries_are_excluded(self):
        # A `category: null` entry is the LLM's own "none of these fit" --
        # a real answer, but not one that should ever override anything, so
        # it's excluded from the dict classify_paper() checks (an absent
        # key and an explicit null both fall through to misc the same way).
        self.labels_file.write_text(json.dumps({
            "somepaper": {"category": "object-detection"},
            "otherpaper": {"category": None},
        }), encoding="utf-8")
        self.assertEqual(cl.load_llm_category_labels(), {"somepaper": "object-detection"})

    def test_stale_category_ids_are_dropped_when_valid_ids_given(self):
        # Confirmed real bug: splitting object-detection -> -2d/-3d and
        # mapping-localization -> mapping/localization left 161 old entries
        # in category_labels_llm.json pointing at ids that no longer exist
        # in categories.json. Passing the current valid id set discards
        # those instead of leaking a dead id back out as a literal category.
        self.labels_file.write_text(json.dumps({
            "stalepaper": {"category": "object-detection"},
            "freshpaper": {"category": "object-detection-2d"},
        }), encoding="utf-8")
        self.assertEqual(
            cl.load_llm_category_labels(valid_ids=frozenset({"object-detection-2d", "object-detection-3d"})),
            {"freshpaper": "object-detection-2d"},
        )


class TestClassifyPaper(unittest.TestCase):
    CATEGORIES = [
        {"id": "object-detection", "keywords": ["object detection", "detector"]},
        {"id": "segmentation", "keywords": ["segmentation", "segment"]},
    ]

    def test_av_paper_with_no_category_match_becomes_misc_not_uncategorized(self):
        # An AV paper is confirmed on-topic (it matched an explicit AV
        # phrase) even when no category keyword fits -- "misc" is a real,
        # visible bucket for that, distinct from "uncategorized" (see
        # DECISIONS.md's "Misc vs uncategorized" entry). Before this
        # fallback existed, this case landed in "uncategorized" instead.
        category, relevance = cl.classify_paper(
            "Notes on Autonomous Driving", "Some notes about autonomous driving methods.", self.CATEGORIES
        )
        self.assertEqual(category, "misc")
        self.assertEqual(relevance, "AV")

    def test_non_av_paper_with_no_category_match_stays_uncategorized(self):
        # The opposite case: no category keyword AND no AV signal at all --
        # relevance itself is the weaker signal here, so this stays
        # "uncategorized" rather than being promoted to a real bucket.
        category, relevance = cl.classify_paper(
            "Notes on Widget Sorting Methods", "Some notes about ways to sort widgets.", self.CATEGORIES
        )
        self.assertEqual(category, "uncategorized")
        self.assertEqual(relevance, "non-AV")

    def test_survey_gate_wins_over_topic_keywords(self):
        # "A Survey of Object Detection" mentions "detection" enough that
        # ordinary keyword scoring would otherwise claim it for
        # object-detection -- the survey gate runs first (see
        # classify_paper's docstring for the priority order).
        category, _ = cl.classify_paper(
            "A Survey of Object Detection Methods for Autonomous Driving",
            "This survey reviews detector architectures. Detector, detector, detection.",
            self.CATEGORIES,
        )
        self.assertEqual(category, "survey-review-paper")

    def test_survey_gate_yields_to_radar_gate(self):
        # The radar gate runs BEFORE the survey gate -- a radar survey stays
        # in radar-perception, consistent with is_radar_paper's own "keep
        # every radar paper in one bucket" reasoning (see classify_paper).
        category, _ = cl.classify_paper(
            "A Survey of Automotive Radar Perception Methods",
            "We review automotive radar-based perception.",
            self.CATEGORIES,
        )
        self.assertEqual(category, "radar-perception")

    def test_last_resort_category_yields_to_a_normal_category_match(self):
        # Real case that motivated LAST_RESORT_CATEGORY_IDS: a last-resort
        # category is never even scored against the normal ones -- it's
        # ranked in a wholly separate second pass, only reached when the
        # first pass matched nothing at all. So no repeat-count of
        # "explainable"/"interpretable" in the abstract can outscore a
        # normal category's topic phrase, however many times it appears
        # (here: 3x "interpretable" + 2x "explainable" vs. 1x "object
        # detection"). See classify_paper's own comment on the second pass.
        categories = self.CATEGORIES + [
            {"id": "explainability", "keywords": ["explainable", "interpretable"]},
        ]
        category, _ = cl.classify_paper(
            "Interpretable Object Detection",
            "We propose an interpretable, explainable, and interpretable object detector. "
            "Our interpretable method is explainable.",
            categories,
        )
        self.assertEqual(category, "object-detection")

    def test_last_resort_category_used_when_nothing_else_matches(self):
        category, relevance = cl.classify_paper(
            "An Explainable Approach for Autonomous Driving",
            "We propose an interpretable and explainable approach for autonomous driving.",
            self.CATEGORIES + [{"id": "explainability", "keywords": ["explainable", "interpretable"]}],
        )
        self.assertEqual(category, "explainability")
        self.assertEqual(relevance, "AV")

    def test_category_independent_of_relevance(self):
        # A generic segmentation paper with no AV terms at all: categorized,
        # but not AV -- category keywords must never leak into relevance.
        category, relevance = cl.classify_paper(
            "Semantic Segmentation of Indoor Scenes", "We segment rooms into furniture classes.",
            self.CATEGORIES,
        )
        self.assertEqual(category, "segmentation")
        self.assertEqual(relevance, "non-AV")

    def test_returns_two_values(self):
        result = cl.classify_paper("X", "Y", self.CATEGORIES)
        self.assertEqual(len(result), 2)

    def test_looks_up_llm_titles_by_normalized_title(self):
        category, relevance = cl.classify_paper(
            "Fast Image Classification", "We classify images of cats and dogs.",
            self.CATEGORIES, llm_av_titles={cl.normalize_title("Fast Image Classification")},
        )
        self.assertEqual(relevance, "AV")

    def test_llm_category_label_used_only_when_nothing_else_matches(self):
        # See fetch_llm_category_labels.py: a last-resort signal, weaker
        # than even the explainability tier, for title-only papers no
        # keyword-based path -- real or last-resort -- could reach.
        category, _ = cl.classify_paper(
            "Some Unmatchable AV Paper Title", None, self.CATEGORIES,
            llm_category_labels={cl.normalize_title("Some Unmatchable AV Paper Title"): "object-detection"},
        )
        self.assertEqual(category, "object-detection")

    def test_llm_category_label_does_not_override_a_real_keyword_match(self):
        category, _ = cl.classify_paper(
            "Semantic Segmentation of Road Scenes", "We segment the road.", self.CATEGORIES,
            llm_category_labels={cl.normalize_title("Semantic Segmentation of Road Scenes"): "object-detection"},
        )
        self.assertEqual(category, "segmentation")

    def test_motion_planner_matches_planner_not_just_planning(self):
        # Real case, confirmed by the user: "End-To-End Interpretable Neural
        # Motion Planner" was landing in "tracking" because a bare
        # "planning" keyword never matches the word "planner" (different
        # word, not a substring) -- the taxonomy's end-to-end-driving
        # category keywords must include "motion planner"/"path planner"
        # phrasing directly, not just "-ing" forms, for this to categorize
        # correctly. This is a real-taxonomy check (loads the actual
        # categories.json), not the tiny local CATEGORIES fixture above.
        real_categories = json.loads((Path(__file__).resolve().parent.parent.parent
                                       / "data" / "categories.json").read_text(encoding="utf-8"))["categories"]
        category, _ = cl.classify_paper(
            "End-To-End Interpretable Neural Motion Planner",
            "We propose a neural motion planner that jointly reasons about detection, prediction, and motion "
            "planning for autonomous driving in an end-to-end interpretable manner.",
            real_categories,
        )
        self.assertEqual(category, "end-to-end-driving")


if __name__ == "__main__":
    unittest.main()
