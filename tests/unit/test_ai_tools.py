"""Набор тулов агента: SQL-тул не тронут, get_bookings зарегистрирован."""

from tg_studio.modules.ai import calendar_tools
from tg_studio.modules.ai.queries import TOOL_REGISTRY
from tg_studio.modules.ai.tools import AGENT_TOOLS, ANALYTICS_TOOLS, BOOKINGS_TOOLS


def test_registry_has_bookings_tool():
    assert TOOL_REGISTRY["get_bookings"] is calendar_tools.get_bookings
    assert TOOL_REGISTRY["execute_analytics_sql"] is not None


def test_agent_tools_merge_both_definitions():
    assert [t["function"]["name"] for t in AGENT_TOOLS] == [
        "execute_analytics_sql",
        "get_bookings",
    ]


def test_sql_tool_definition_untouched():
    assert len(ANALYTICS_TOOLS) == 1
    sql_params = ANALYTICS_TOOLS[0]["function"]["parameters"]
    assert sql_params["required"] == ["sql_query"]


def test_bookings_tool_all_filters_optional():
    params = BOOKINGS_TOOLS[0]["function"]["parameters"]
    assert params["required"] == []
    assert set(params["properties"]) == {"master", "date_from", "date_to"}


def test_client_variant_has_no_sql_tool():
    """Клиентскому агенту SQL физически недоступен — в tools только get_bookings."""
    from tg_studio.modules.ai.api import VARIANT_TOOLS

    client_names = [t["function"]["name"] for t in VARIANT_TOOLS["client"]]
    assert client_names == ["get_bookings"]
    owner_names = [t["function"]["name"] for t in VARIANT_TOOLS["owner"]]
    assert "execute_analytics_sql" in owner_names and "get_bookings" in owner_names
