import unittest

from feeder import topics

STARTING_TREE = {
    "cybersecurity": ["vulnerabilities", "incidents", "ransomware", "frameworks", "identity", "cloud", "ot-ics"],
    "regulation": ["federal-rulemaking", "state-law", "enforcement", "eu", "international"],
    "privacy": ["us-state-privacy", "gdpr", "children-student", "health-data"],
    "standards": ["nist", "iso", "fedramp", "cmmc", "pci", "soc2"],
    "technology": ["ai", "cloud", "telecom", "software-supply-chain", "devices"],
    "education": ["k12", "higher-ed", "edtech", "student-data", "funding"],
    "healthcare": ["hipaa", "providers", "health-it"],
    "finance": ["banking", "securities", "insurance", "safeguards-rule", "private-equity"],
    "legal": ["courts", "law-firms"],
    "manufacturing": ["industrial", "supply-chain"],
    "government": ["federal", "florida", "local"],
    "marketing": ["seo", "social", "email", "analytics", "advertising"],
    "business": ["msp-industry", "m-and-a", "workforce"],
}


class TopicsTests(unittest.TestCase):
    def test_registry_matches_the_starting_tree_exactly(self):
        registry = topics.load_topics()
        self.assertEqual(set(registry), set(STARTING_TREE))
        for topic, subs in STARTING_TREE.items():
            self.assertEqual(registry[topic]["subtopics"], subs, topic)
            self.assertTrue(registry[topic]["reason"], f"{topic} carries a one-line reason")
            self.assertRegex(registry[topic]["added"], r"^\d{4}-\d{2}-\d{2}$")

    def test_tags_and_validity(self):
        self.assertTrue(topics.is_valid_tag("cybersecurity"))
        self.assertTrue(topics.is_valid_tag("standards/nist"))
        self.assertFalse(topics.is_valid_tag("standards/owasp"))
        self.assertFalse(topics.is_valid_tag("nist"))
        self.assertEqual(topics.split_tag("privacy/gdpr"), ("privacy", "gdpr"))
        self.assertEqual(topics.split_tag("privacy"), ("privacy", None))
        self.assertEqual(len(topics.all_tags()), 13 + sum(len(v) for v in STARTING_TREE.values()))


if __name__ == "__main__":
    unittest.main()
