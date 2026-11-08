import io
import csv
import pytest
from leadlift.ui import _sanitize_csv_value


def test_sanitize_csv_value_dangerous_prefixes():
    assert _sanitize_csv_value("=1+1") == "'=1+1"
    assert _sanitize_csv_value("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert _sanitize_csv_value("-1+2") == "'-1+2"
    assert _sanitize_csv_value("@SUM(A1:A2)") == "'@SUM(A1:A2)"


def test_sanitize_csv_value_whitespace_prefixes():
    assert _sanitize_csv_value(" =1") == "' =1"
    assert _sanitize_csv_value("\t=1") == "'\t=1"
    assert _sanitize_csv_value("\r=1") == "'\r=1"
    assert _sanitize_csv_value("\n=1") == "'\n=1"


def test_sanitize_csv_value_ordinary_values():
    assert _sanitize_csv_value("Alice") == "Alice"
    assert _sanitize_csv_value("alice@example.com") == "alice@example.com"
    assert _sanitize_csv_value("+1-555-0123") == "'+1-555-0123"
    assert _sanitize_csv_value("-5") == "-5"
    assert _sanitize_csv_value("") == ""
    assert _sanitize_csv_value(None) == ""


def test_sanitize_csv_value_csv_serialization():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([_sanitize_csv_value("=1+1"), _sanitize_csv_value("normal")])
    assert output.getvalue().strip() == "'=1+1,normal"