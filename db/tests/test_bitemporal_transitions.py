"""Phase 3 / Track B: bitemporal transition + belief-history contract tests.

Static, no live database required (mirrors test_migrations.py's style) --
db/tests/test_bitemporal_live.py under backend/tests exercises the real
statements against a running CockroachDB instance.
"""

from pathlib import Path
import re
import unittest


DB_ROOT = Path(__file__).resolve().parents[1]


class BitemporalTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.core_schema = (DB_ROOT / "migrations" / "0002_core_schema.sql").read_text()
        cls.memory_indexes = (
            DB_ROOT / "migrations" / "0003_memory_indexes.sql"
        ).read_text()
        cls.migration_0006 = (
            DB_ROOT / "migrations" / "0006_bitemporal_transitions.sql"
        ).read_text()
        cls.recall_semantic = (
            DB_ROOT / "queries" / "recall_semantic.sql"
        ).read_text()

    def test_bitemporal_columns_already_existed_before_track_b(self) -> None:
        """0006 must not redefine columns 0002 already shipped."""

        for column in ("valid_from", "valid_to", "recorded_at", "superseded_by"):
            self.assertIn(column, self.core_schema)
        self.assertNotIn("ADD COLUMN valid_from", self.migration_0006)
        self.assertNotIn("ADD COLUMN valid_to", self.migration_0006)
        self.assertNotIn("ADD COLUMN recorded_at", self.migration_0006)
        self.assertNotIn("ADD COLUMN superseded_by", self.migration_0006)

    def test_currently_valid_partial_index_already_existed_before_track_b(self) -> None:
        """0003 already ships the "what do we believe now" partial index; 0006
        must not recreate it, only build on top of it.
        """

        self.assertRegex(
            self.memory_indexes,
            r"semantic_current\s*\n\s*ON semantic_facts \(org_id, subject, predicate\)"
            r"[\s\S]*WHERE valid_to IS NULL",
        )
        # 0006 may reference/document the existing index (and does, in its
        # header comment and the semantic_facts_current view's comment) but
        # must not redefine it.
        self.assertNotIn("CREATE INDEX IF NOT EXISTS semantic_current", self.migration_0006)

    def test_0006_adds_a_covering_history_index(self) -> None:
        self.assertRegex(
            self.migration_0006,
            r"CREATE INDEX IF NOT EXISTS semantic_facts_history\s*\n"
            r"\s*ON semantic_facts \(org_id, subject, predicate, recorded_at DESC\)",
        )
        self.assertIn("superseded_by", self.migration_0006)

    def test_0006_enforces_transition_not_overwrite_invariant(self) -> None:
        """A fact can never be superseded while still open (valid_to NULL) --
        this is the schema-level guarantee that facts evolve as transitions.
        """

        self.assertRegex(
            self.migration_0006,
            r"CHECK\s*\(superseded_by IS NULL OR valid_to IS NOT NULL\)",
        )

    def test_0006_exposes_current_and_history_views(self) -> None:
        self.assertIn("CREATE VIEW IF NOT EXISTS semantic_facts_current", self.migration_0006)
        self.assertIn(
            "CREATE VIEW IF NOT EXISTS semantic_facts_belief_history", self.migration_0006
        )

    def test_0006_contains_no_cluster_settings(self) -> None:
        """Cluster settings never belong in app migrations (db/bootstrap owns
        them) -- and this migration specifically must not silently flip the
        multi-mutation guard on, see its own header comment. Scoped to 0006
        only: whether *other* migrations respect this is
        test_migrations.py::test_cluster_setting_is_kept_out_of_app_migrations's
        contract, owned outside Track B.
        """

        self.assertNotIn("SET CLUSTER SETTING", self.migration_0006)

    def test_0006_is_pure_ddl_no_dml(self) -> None:
        executable = "\n".join(
            line
            for line in self.migration_0006.splitlines()
            if not line.lstrip().startswith("--")
        )
        self.assertNotRegex(executable.upper(), r"\bINSERT INTO\b")
        self.assertNotRegex(executable.upper(), r"\bUPDATE\s+SEMANTIC_FACTS\b")

    def test_recall_semantic_gates_on_valid_time_and_system_time(self) -> None:
        self.assertIn("valid_from <= $3", self.recall_semantic)
        self.assertIn("valid_to IS NULL OR valid_to > $3", self.recall_semantic)
        self.assertIn("recorded_at <= $3", self.recall_semantic)

    def test_recall_semantic_exposes_superseded_predecessor(self) -> None:
        self.assertIn("superseded_by", self.recall_semantic)
        self.assertIn("AS predecessor", self.recall_semantic)
        self.assertIn("predecessor.superseded_by = nearest.fact_id", self.recall_semantic)

    def test_recall_semantic_still_scopes_by_org_agent_and_service(self) -> None:
        required = ("org_id = $1", "agent_id = $2", "$6")
        for fragment in required:
            self.assertIn(fragment, self.recall_semantic)


if __name__ == "__main__":
    unittest.main()
