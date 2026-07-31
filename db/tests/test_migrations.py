from pathlib import Path
import re
import unittest


DB_ROOT = Path(__file__).resolve().parents[1]


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = (DB_ROOT / "migrations" / "0002_core_schema.sql").read_text()
        cls.indexes = (DB_ROOT / "migrations" / "0003_memory_indexes.sql").read_text()
        cls.bootstrap = (DB_ROOT / "bootstrap" / "001_enable_cspann.sql").read_text()
        cls.query = (DB_ROOT / "queries" / "rollback_and_record.sql").read_text()

    def test_cluster_setting_is_kept_out_of_app_migrations(self) -> None:
        migrations = "\n".join(
            path.read_text() for path in sorted((DB_ROOT / "migrations").glob("*.sql"))
        )
        self.assertNotIn("SET CLUSTER SETTING", migrations)
        self.assertIn("feature.vector_index.enabled", self.bootstrap)

    def test_core_operational_and_memory_tables_are_colocated(self) -> None:
        required = {
            "services",
            "service_dependencies",
            "deploys",
            "slos",
            "metric_samples",
            "incidents",
            "alerts",
            "orders",
            "config_values",
            "remediation_actions",
            "episodic_events",
            "semantic_facts",
            "procedural_memory",
            "session_state",
        }
        created = set(
            re.findall(
                r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)",
                self.schema,
                flags=re.IGNORECASE,
            )
        )
        self.assertTrue(required <= created, required - created)

    def test_every_embedding_uses_locked_dimension(self) -> None:
        dimensions = re.findall(r"VECTOR\((\d+)\)", self.schema + self.indexes)
        self.assertTrue(dimensions)
        self.assertEqual({"1024"}, set(dimensions))

    def test_all_memory_types_have_cosine_cspann_indexes(self) -> None:
        for table in ("episodic_events", "semantic_facts", "procedural_memory"):
            self.assertRegex(
                self.indexes,
                rf"ON\s+{table}\s*\([^;]+embedding vector_cosine_ops",
            )
        self.assertEqual(3, self.indexes.count("CREATE VECTOR INDEX"))

    def test_atomic_query_writes_action_and_memory_in_one_statement(self) -> None:
        self.assertIn("INSERT INTO episodic_events", self.query)
        self.assertIn("INSERT INTO remediation_actions", self.query)
        self.assertIn("UPDATE services", self.query)
        self.assertIn("inc.status IN ('open', 'mitigating')", self.query)
        self.assertIn("rb.status = 'active'", self.query)
        executable_sql = "\n".join(
            line for line in self.query.splitlines() if not line.lstrip().startswith("--")
        )
        self.assertNotRegex(executable_sql.upper(), r"\b(COMMIT|BEGIN)\b")
        statements = [part for part in executable_sql.split(";") if part.strip()]
        self.assertEqual(1, len(statements))

    def test_record_action_and_final_select_are_gated_not_phantom_rows(self) -> None:
        """DB#1: record_action's INSERT ... SELECT must draw its row FROM
        record_episode (so it runs zero times when the upstream
        incident/runbook/provenance gate in action_context rejected the
        request), and the query's terminal SELECT must draw FROM the same
        upstream CTEs -- not a bare scalar subquery with no FROM clause,
        which always evaluates to exactly one (NULL-padded) row regardless
        of whether anything upstream actually matched. That bare-scalar
        shape is exactly the bug: a caller sees a 'success' response for a
        rollback that never happened.
        """

        self.assertIn(
            "record_action AS (\n    INSERT INTO remediation_actions", self.query
        )
        self.assertIn(
            "record_episode.event_id, $9\n    FROM record_episode", self.query
        )
        # The old buggy shape: a scalar subquery inline in the SELECT list,
        # with no FROM clause gating the INSERT on record_episode existing.
        self.assertNotIn("(SELECT event_id FROM record_episode), $9", self.query)
        final_select = self.query.rsplit(")\nSELECT", 1)[-1]
        self.assertIn("FROM rollback_deploy", final_select)
        self.assertIn("CROSS JOIN record_episode", final_select)
        self.assertIn("CROSS JOIN record_action", final_select)
        # No bare scalar subqueries (`(SELECT ... FROM <cte>)`) remain in the
        # terminal SELECT's column list -- every value is a gated join column.
        self.assertNotRegex(final_select, r"\(SELECT\s+\w+\s+FROM\s+\w+\)")


if __name__ == "__main__":
    unittest.main()
