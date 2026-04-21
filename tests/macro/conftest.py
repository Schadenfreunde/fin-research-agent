# tests/macro/conftest.py
import pathlib
import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


@pytest.fixture
def validated_source_package():
    return load_fixture("validated_source_package.txt")


@pytest.fixture
def data_manifest():
    return load_fixture("data_manifest.txt")


@pytest.fixture
def analyst_high_conviction():
    return load_fixture("analyst_output_high_conviction.txt")


@pytest.fixture
def analyst_low_conviction():
    return load_fixture("analyst_output_low_conviction.txt")


@pytest.fixture
def analyst_thematic():
    return load_fixture("analyst_output_thematic.txt")


@pytest.fixture
def synthesis_document_valid():
    return load_fixture("synthesis_document_valid.txt")
