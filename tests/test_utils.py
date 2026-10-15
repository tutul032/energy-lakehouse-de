import pytest

from energy_lakehouse.utils import table_name


def test_three_part_name():
    assert table_name("workspace", "energy_dev", "load") == "`workspace`.`energy_dev`.`load`"


def test_two_part_schema_name():
    assert table_name("workspace", "energy_dev") == "`workspace`.`energy_dev`"


def test_leading_underscore_and_hyphen_are_kept():
    assert table_name("workspace", "energy-dev", "_smoke_test") == (
        "`workspace`.`energy-dev`.`_smoke_test`"
    )


def test_backticks_are_escaped():
    assert table_name("workspace", "energy_dev", "we`ird") == "`workspace`.`energy_dev`.`we``ird`"


@pytest.mark.parametrize(
    ("catalog", "schema", "table"),
    [("", "s", "t"), ("c", "", "t"), ("c", "s", ""), ("c", "s", "   ")],
)
def test_empty_parts_rejected(catalog, schema, table):
    with pytest.raises(ValueError):
        table_name(catalog, schema, table)
