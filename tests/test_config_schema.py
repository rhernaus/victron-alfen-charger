from alfen_driver.config_schema import get_config_schema


def test_get_config_schema_has_sections_and_known_fields() -> None:
    schema = get_config_schema()
    assert isinstance(schema, dict)
    assert "sections" in schema
    sections = schema["sections"]
    # Spot-check a few entries
    assert "modbus" in sections
    assert "defaults" in sections
    assert "controls" in sections
    assert "tibber" in sections
    assert "web" in sections
    assert sections["modbus"]["type"] == "object"
    assert "fields" in sections["modbus"]
    assert "ip" in sections["modbus"]["fields"]
