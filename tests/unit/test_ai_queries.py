"""Unit tests for LLM SQL validation (modules/ai/queries)."""

import pytest

from tg_studio.modules.ai.queries import validate_sql


class TestValidateSQL:
    def test_valid_simple_select_with_placeholder(self) -> None:
        ok, err = validate_sql(
            "SELECT COUNT(*) AS cnt FROM services WHERE business_id = :business_id"
        )
        assert ok is True
        assert err == ""

    def test_valid_bookings_with_business_id(self) -> None:
        """bookings now has business_id directly — should pass."""
        ok, err = validate_sql(
            "SELECT COUNT(*) AS cnt FROM bookings WHERE business_id = :business_id"
        )
        assert ok is True
        assert err == ""

    def test_rejects_empty(self) -> None:
        ok, err = validate_sql("   ")
        assert ok is False
        assert "Empty" in err

    def test_rejects_non_select(self) -> None:
        ok, err = validate_sql("UPDATE masters SET is_active = false")
        assert ok is False
        assert "SELECT" in err

    def test_rejects_dangerous_keyword(self) -> None:
        ok, err = validate_sql("SELECT 1; DROP TABLE masters; --")
        assert ok is False
        assert "forbidden" in err

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM services",
        ],
    )
    def test_requires_business_id_placeholder(self, sql: str) -> None:
        ok, err = validate_sql(sql)
        assert ok is False
        assert "business_id" in err
