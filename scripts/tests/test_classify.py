#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for classify.py's category + AV-relevance logic.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_classify.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classify as cl


class TestClassifyRelevance(unittest.TestCase):
    def test_title_mention_is_core(self):
        self.assertEqual(
            cl.classify_relevance("PlanNet: Planning for Autonomous Driving", "A method for planning."),
            "core",
        )

    def test_driving_as_a_standalone_title_word_is_core(self):
        # DriveLM: the compound "DriveLM" itself doesn't match any
        # AV_RELEVANCE_TERMS phrase, but "Driving" appears standalone
        # elsewhere in the title -- this must not fall through to "adjacent".
        self.assertEqual(
            cl.classify_relevance(
                "DriveLM: Driving with Graph Visual Question Answering",
                "A framework connecting perception to planning.",
            ),
            "core",
        )

    def test_data_driven_does_not_false_positive_on_driven(self):
        # "data-driven" / "goal-driven" are extremely common ML title
        # phrases unrelated to vehicles -- must not match "drive"/"driving".
        self.assertEqual(
            cl.classify_relevance(
                "A Data-Driven Approach to Image Classification",
                "We classify images using a data-driven neural network.",
            ),
            "adjacent",
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
            "core",
        )

    def test_no_av_terms_is_adjacent(self):
        self.assertEqual(
            cl.classify_relevance("Fast Image Classification", "We classify images of cats and dogs."),
            "adjacent",
        )

    def test_missing_abstract_does_not_crash(self):
        self.assertEqual(cl.classify_relevance("Some Paper", None), "adjacent")

    def test_traffic_light_and_sign_count_as_av_relevant(self):
        # user-flagged real miss: "A VT-HMM-Based Framework for Countdown
        # Timer Traffic Light State Estimation" had no abstract and no other
        # AV-specific phrase in its title, so it fell through to "adjacent"
        # despite being unambiguously about real-world driving
        # infrastructure.
        self.assertEqual(
            cl.classify_relevance("Countdown Timer Traffic Light State Estimation", None),
            "core",
        )
        self.assertEqual(
            cl.classify_relevance("Robust Traffic Sign Recognition in Adverse Weather", None),
            "core",
        )

    def test_dataset_name_alone_counts_as_av_relevant(self):
        self.assertEqual(
            cl.classify_relevance("A New Benchmark", "We evaluate on nuScenes and compare to prior work."),
            "core",
        )

    def test_llm_core_promotes_a_keyword_adjacent_paper(self):
        self.assertEqual(
            cl.classify_relevance(
                "Fast Image Classification", "We classify images of cats and dogs.", llm_says_core=True,
            ),
            "core",
        )

    def test_llm_adjacent_does_not_promote(self):
        self.assertEqual(
            cl.classify_relevance(
                "Fast Image Classification", "We classify images of cats and dogs.", llm_says_core=False,
            ),
            "adjacent",
        )

    # -- Ground-truth regression cases (real papers, user-confirmed) --------

    def test_mechanical_chassis_control_paper_excluded(self):
        # Real paper, confirmed by the user: was wrongly "core" purely
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
            "adjacent",
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
            "core",
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
        self.assertEqual(relevance, "core")

    def test_unlisted_dataset_paper_still_scored_normally(self):
        # The override is exact-title, not "any paper that sounds like a
        # dataset paper" -- one not on the known list falls through to
        # ordinary keyword scoring, same as before this feature existed.
        category, _ = cl.classify_paper(
            "Some Other Dataset Paper", "We introduce detection and tracking benchmarks. Tracking tracking.",
            self.CATEGORIES, known_dataset_titles=frozenset(),
        )
        self.assertEqual(category, "tracking")

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


class TestClassifyPaper(unittest.TestCase):
    CATEGORIES = [
        {"id": "object-detection", "keywords": ["object detection", "detector"]},
        {"id": "segmentation", "keywords": ["segmentation", "segment"]},
    ]

    def test_uncategorized_when_no_keywords_match(self):
        category, relevance = cl.classify_paper(
            "Autonomous Driving Survey", "A survey of autonomous driving methods.", self.CATEGORIES
        )
        self.assertEqual(category, "uncategorized")
        self.assertEqual(relevance, "core")

    def test_category_independent_of_relevance(self):
        # A generic segmentation paper with no AV terms at all: categorized,
        # but not core -- category keywords must never leak into relevance.
        category, relevance = cl.classify_paper(
            "Semantic Segmentation of Indoor Scenes", "We segment rooms into furniture classes.",
            self.CATEGORIES,
        )
        self.assertEqual(category, "segmentation")
        self.assertEqual(relevance, "adjacent")

    def test_returns_two_values(self):
        result = cl.classify_paper("X", "Y", self.CATEGORIES)
        self.assertEqual(len(result), 2)

    def test_looks_up_llm_titles_by_normalized_title(self):
        category, relevance = cl.classify_paper(
            "Fast Image Classification", "We classify images of cats and dogs.",
            self.CATEGORIES, llm_core_titles={cl.normalize_title("Fast Image Classification")},
        )
        self.assertEqual(relevance, "core")

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
