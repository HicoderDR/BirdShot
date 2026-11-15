# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gallery_policy import bird_state


class GateTests(unittest.TestCase):
    def evidence(self, **changes):
        return {"status": "completed", "scoreType": "birder-fullclass", "classCount": 10000,
                "top1IsBird": True, "birdMass": .9, "nonBirdMass": .1, "maxBird": .7,
                "maxNonBird": .02, "margin": .68, "detectionConfidence": .9, **changes}

    def test_closed_set_species_score_is_not_bird_evidence(self):
        state, _ = bird_state(self.evidence(top1IsBird=False, birdMass=.05, nonBirdMass=.95,
                                           maxBird=.01, maxNonBird=.4, margin=-.39, speciesSoftmax=.999))
        self.assertEqual(state, "nonBird")

    def test_ambiguous_failed_and_low_confidence_are_unknown(self):
        for evidence in [None, {"status": "failed"},
                         self.evidence(birdMass=.85, maxBird=.15, margin=.14),
                         self.evidence(detectionConfidence=.4),
                         {"status": "completed", "scoreType": "old-bioclip-cache"}]:
            self.assertEqual(bird_state(evidence)[0], "unknown")

    def test_bird_requires_competing_evidence_and_detector_agreement(self):
        self.assertEqual(bird_state(self.evidence())[0], "bird")


if __name__ == "__main__":
    unittest.main()
