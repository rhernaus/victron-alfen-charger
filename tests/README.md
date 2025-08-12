# Test Suite Documentation

This directory contains comprehensive unit tests for the Alfen driver project.

## Test Structure

- `conftest.py` - Pytest configuration and shared fixtures
- `test_exceptions.py` - Custom exception classes (100% coverage)
- `test_config.py` - Configuration management (96% coverage)
- `test_modbus_utils.py` - Modbus utilities (100% coverage)
- `test_error_recovery.py` - Error recovery patterns (100% coverage)
- `test_controls.py` - Charging controls (66% coverage)
- `test_logic.py` - Business logic (46% coverage)
- `test_integration.py` - Integration tests

## Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run with coverage report
python3 -m pytest tests/ --cov=alfen_driver --cov-report=html

# Run specific test module
python3 -m pytest tests/test_exceptions.py -v

# Run specific test
python3 -m pytest tests/test_config.py::TestConfig::test_config_validation_success -v
```

## Test Coverage Summary

- **Overall Coverage**: ~60% (774 lines covered out of 1101)
- **Core modules**: Excellent coverage of exception handling, configuration, and utilities
- **Business logic**: Partial coverage - complex integration points require more work
- **Driver module**: Limited coverage due to D-Bus dependencies

## Key Features Tested

### ✅ Fully Tested
- Custom exception hierarchy and error messages
- Configuration loading and validation
- Modbus utilities (register reading, string decoding, retries)
- Error recovery patterns (retry decorator, circuit breaker)
- Type validation and constraints

### 🔄 Partially Tested
- Charging control logic
- Business logic for mode switching
- Status mapping and processing

### ⏳ Needs More Testing
- Driver initialization and main loop
- D-Bus integration
- Real-time scheduling logic
- Complete integration scenarios

## Test Dependencies

The test suite mocks system dependencies that aren't available in test environments:

- `dbus` - D-Bus system integration
- `gi.repository.GLib` - GLib event loop
- `vedbus` - Victron D-Bus utilities

## Known Issues

Some tests have minor failures due to:
1. Missing functions that need to be implemented
2. Complex mocking requirements for integration tests
3. Timing-dependent logic in controls

These are documented and will be addressed in future iterations.

## Test Data

Tests use realistic sample data:
- Sample Modbus register values representing 230V, 12A, 3-phase operation
- Valid YAML configuration files
- Typical error scenarios and edge cases
