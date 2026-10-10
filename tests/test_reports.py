"""Test HK ticker normalization and an offline local report registry."""

from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage import (
    ReportRecord,
    list_reports,
    load_registry,
    normalize_hk_ticker,
    register_report,
)


def synthetic_pdf(label: str = "SYNTHETIC TEST DOCUMENT") -> bytes:
    """Build a one-page PDF from invented text, with xref and trailer entries."""
    content = f"BT /F1 12 Tf 30 140 Td ({label}) Tj ET\n".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"endstream",
    )
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(document)


class HongKongTickerTests(unittest.TestCase):
    def test_normalizes_supported_formats(self) -> None:
        for value, expected in (
            (700, "00700"), ("700", "00700"), ("0700", "00700"),
            ("00700", "00700"), ("0700.HK", "00700"),
            ("9988.HK", "09988"), (1810, "01810"),
            ("  0700.hk\t", "00700"), ("00001", "00001"),
            (99999, "99999"),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_hk_ticker(value), expected)

    def test_normalization_is_idempotent(self) -> None:
        canonical = normalize_hk_ticker("1810.HK")
        self.assertEqual(normalize_hk_ticker(canonical), canonical)

    def test_rejects_invalid_codes(self) -> None:
        for invalid in (
            "", " ", "0", 0, "00000", -700, "-700", "+700", "007000",
            100000, "700.0", "0.700", "700.US", "HK700", "700.HK.HK",
            "700 .HK", "700.HK", "７００", "٧٠٠", "70\n0", "7 00",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    normalize_hk_ticker(invalid)

    def test_rejects_non_integer_or_string_inputs(self) -> None:
        for invalid in (True, False, 700.0, None, [], {}, b"0700"):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    normalize_hk_ticker(invalid)  # type: ignore[arg-type]


class LocalReportRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage reports ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC financial report.pdf"
        self.pdf.write_bytes(synthetic_pdf())
        self.registry = self.directory / "registry" / "reports.json"
        self.metadata: dict[str, str] = {
            "ticker": "0700.HK",
            "company": "Synthetic HK Company",
            "report_type": "annual",
            "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented test report",
        }

    def register(self, **changes: object) -> ReportRecord:
        arguments: dict[str, object] = dict(self.metadata, registry_path=self.registry)
        arguments.update(changes)
        return register_report(self.pdf, **arguments)  # type: ignore[arg-type]

    def test_registers_pdf_and_computes_sha256(self) -> None:
        record = self.register(publication_date="2025-03-20")

        self.assertIsInstance(record, ReportRecord)
        self.assertEqual(record.ticker, "00700")
        self.assertEqual(record.company, self.metadata["company"])
        self.assertEqual(record.report_type, "annual")
        self.assertEqual(record.fiscal_period, "FY2024")
        self.assertEqual(record.publication_date, "2025-03-20")
        self.assertEqual(record.source_name, self.metadata["source_name"])
        self.assertEqual(record.local_file, str(self.pdf.resolve()))
        self.assertEqual(record.file_sha256, hashlib.sha256(self.pdf.read_bytes()).hexdigest())

    def test_optional_publication_date_defaults_to_none(self) -> None:
        self.assertIsNone(self.register().publication_date)

    def test_record_is_immutable(self) -> None:
        record = self.register()
        with self.assertRaises(FrozenInstanceError):
            record.company = "Other Company"  # type: ignore[misc]

    def test_registry_round_trip_uses_versioned_json(self) -> None:
        record = self.register()
        document = json.loads(self.registry.read_text(encoding="utf-8"))

        self.assertEqual(document, {"version": 1, "reports": [asdict(record)]})
        self.assertEqual(load_registry(self.registry), [record])
        self.assertEqual(list_reports("700", registry_path=self.registry), [record])

    def test_registration_keeps_original_file_without_copying_it(self) -> None:
        original_bytes = self.pdf.read_bytes()
        self.register()

        self.assertEqual(self.pdf.read_bytes(), original_bytes)
        self.assertEqual(list(self.directory.rglob("*.pdf")), [self.pdf])

    def test_missing_registry_lists_empty_without_creating_file(self) -> None:
        self.assertEqual(load_registry(self.registry), [])
        self.assertEqual(list_reports("700", registry_path=self.registry), [])
        self.assertFalse(self.registry.exists())
        self.assertFalse(self.registry.parent.exists())

    def test_listing_filters_tickers_and_preserves_all_records(self) -> None:
        first = self.register()
        other_pdf = self.directory / "SYNTHETIC other company.pdf"
        other_pdf.write_bytes(synthetic_pdf("SYNTHETIC OTHER COMPANY"))
        second = register_report(
            other_pdf, ticker="9988.HK", company="Other Synthetic HK Company",
            report_type="interim", fiscal_period="H12025", source_name="SYNTHETIC test",
            registry_path=self.registry,
        )

        self.assertEqual(list_reports("00700", registry_path=self.registry), [first])
        self.assertEqual(list_reports("9988", registry_path=self.registry), [second])
        self.assertEqual(list_reports("1810", registry_path=self.registry), [])
        self.assertCountEqual(load_registry(self.registry), [first, second])

    def test_listing_is_stable_across_registry_input_order(self) -> None:
        first = self.register()
        later_pdf = self.directory / "SYNTHETIC later report.pdf"
        later_pdf.write_bytes(synthetic_pdf("SYNTHETIC LATER REPORT"))
        second = register_report(
            later_pdf, **dict(self.metadata, fiscal_period="FY2025"), registry_path=self.registry
        )
        before = list_reports("700", registry_path=self.registry)
        self.registry.write_text(
            json.dumps({"version": 1, "reports": [asdict(second), asdict(first)]}),
            encoding="utf-8",
        )

        self.assertEqual(list_reports("700", registry_path=self.registry), before)

    def test_duplicate_registration_does_not_rewrite_registry(self) -> None:
        first = self.register()
        saved = self.registry.read_bytes()
        second = self.register(ticker="700")

        self.assertEqual(second, first)
        self.assertEqual(load_registry(self.registry), [first])
        self.assertEqual(self.registry.read_bytes(), saved)

    def test_duplicate_bytes_at_another_path_keep_original_traceable_path(self) -> None:
        first = self.register()
        copy = self.directory / "SYNTHETIC byte-identical copy.pdf"
        copy.write_bytes(self.pdf.read_bytes())
        saved = self.registry.read_bytes()
        second = register_report(copy, **self.metadata, registry_path=self.registry)

        self.assertEqual(second, first)
        self.assertEqual(second.local_file, str(self.pdf.resolve()))
        self.assertEqual(self.registry.read_bytes(), saved)

    def test_same_file_with_conflicting_metadata_is_not_silently_replaced(self) -> None:
        self.register()
        saved = self.registry.read_bytes()
        for field, value in (
            ("ticker", "1810"), ("company", "Other Synthetic Company"),
            ("report_type", "interim"), ("fiscal_period", "FY2025"),
            ("source_name", "Other SYNTHETIC source"),
            ("publication_date", "2025-03-20"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.register(**{field: value})
                self.assertEqual(self.registry.read_bytes(), saved)

    def test_existing_metadata_can_be_listed_after_original_pdf_is_removed(self) -> None:
        record = self.register()
        self.pdf.unlink()

        self.assertEqual(list_reports("700", registry_path=self.registry), [record])
        self.assertEqual(record.local_file, str(self.pdf))

    def test_all_supported_report_types_can_be_registered(self) -> None:
        for report_type in ("annual", "interim", "annual_results", "interim_results"):
            with self.subTest(report_type=report_type):
                registry = self.directory / report_type / "reports.json"
                record = self.register(report_type=report_type, registry_path=registry)
                self.assertEqual(record.report_type, report_type)

    def test_missing_file_is_rejected_without_creating_registry(self) -> None:
        self.pdf.unlink()
        with self.assertRaises((OSError, ValueError)) as raised:
            self.register()
        self.assertIn(str(self.pdf), str(raised.exception))
        self.assertFalse(self.registry.exists())

    def test_directory_instead_of_file_is_rejected(self) -> None:
        with self.assertRaises((OSError, ValueError)):
            register_report(self.directory, **self.metadata, registry_path=self.registry)
        self.assertFalse(self.registry.exists())

    def test_files_without_basic_pdf_features_are_rejected(self) -> None:
        for data in (
            b"", b"<html>Not a PDF</html>", b"ordinary text\n%%EOF\n",
            b"%PDF-invalid\n%%EOF\n", b"%PDF-1.4\nmissing end marker\n",
        ):
            with self.subTest(data=data):
                self.pdf.write_bytes(data)
                with self.assertRaises(ValueError) as raised:
                    self.register()
                self.assertIn("PDF", str(raised.exception))
                self.assertFalse(self.registry.exists())

    def test_unreadable_pdf_reports_error_without_creating_registry(self) -> None:
        with patch("metric_mirage.reports.Path.open", side_effect=PermissionError("Access denied")):
            with self.assertRaises((OSError, ValueError)) as raised:
                self.register()
        self.assertIn("Access denied", str(raised.exception))
        self.assertFalse(self.registry.exists())

    def test_invalid_report_metadata_is_rejected_before_save(self) -> None:
        for field, value in (
            ("ticker", "0"), ("company", " "), ("company", 123),
            ("report_type", "quarterly"), ("report_type", ""),
            ("fiscal_period", "\n\t"), ("source_name", ""),
            ("publication_date", "2025-02-30"),
            ("publication_date", "2025-3-2"),
            ("publication_date", "2025-03-02T12:00:00"),
            ("publication_date", ""), ("publication_date", 20250302),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.register(**{field: value})
                self.assertFalse(self.registry.exists())

    def test_invalid_query_ticker_is_rejected(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            list_reports("700.US", registry_path=self.registry)

    def test_malformed_registry_is_never_overwritten(self) -> None:
        self.registry.parent.mkdir()
        for content in (b"{broken JSON", b"\xffnot utf8", b""):
            with self.subTest(content=content):
                self.registry.write_bytes(content)
                with self.assertRaises((TypeError, ValueError)):
                    load_registry(self.registry)
                with self.assertRaises((TypeError, ValueError)):
                    self.register()
                self.assertEqual(self.registry.read_bytes(), content)

    def test_invalid_registry_schema_is_never_overwritten(self) -> None:
        self.registry.parent.mkdir()
        valid_record = {
            "ticker": "00700", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC test", "local_file": str(self.pdf),
            "file_sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
            "publication_date": None,
        }
        invalid_records = []
        for field in valid_record:
            if field != "publication_date":
                incomplete = dict(valid_record)
                del incomplete[field]
                invalid_records.append({"version": 1, "reports": [incomplete]})
        for field, value in (
            ("ticker", "0"), ("file_sha256", "not a checksum"),
            ("file_sha256", "g" * 64), ("local_file", ""),
            ("report_type", "quarterly"), ("publication_date", "2025-02-30"),
        ):
            invalid_records.append({"version": 1, "reports": [dict(valid_record, **{field: value})]})
        for document in (
            [], None, {"version": 2, "reports": []}, {"version": True, "reports": []},
            {"version": 1}, {"version": 1, "reports": {}},
            {"version": 1, "reports": ["not a report"]}, *invalid_records,
        ):
            with self.subTest(document=document):
                saved = json.dumps(document).encode("utf-8")
                self.registry.write_bytes(saved)
                with self.assertRaises((TypeError, ValueError)):
                    load_registry(self.registry)
                with self.assertRaises((TypeError, ValueError)):
                    self.register()
                self.assertEqual(self.registry.read_bytes(), saved)

    def test_registry_read_failure_preserves_existing_bytes(self) -> None:
        self.register()
        saved = self.registry.read_bytes()
        with patch("metric_mirage.reports.Path.read_text", side_effect=PermissionError("Access denied")):
            with self.assertRaises((OSError, ValueError)):
                load_registry(self.registry)
        self.assertEqual(self.registry.read_bytes(), saved)

    def test_atomic_replace_failure_preserves_registry_and_cleans_temporary_files(self) -> None:
        first = self.register()
        saved = self.registry.read_bytes()
        existing_files = set(self.registry.parent.iterdir())
        self.pdf.write_bytes(synthetic_pdf("SYNTHETIC UPDATED REPORT"))
        with patch("metric_mirage.reports.os.replace", side_effect=PermissionError("Write denied")):
            with self.assertRaises((OSError, ValueError)):
                self.register(fiscal_period="FY2025")

        self.assertEqual(self.registry.read_bytes(), saved)
        self.assertEqual(load_registry(self.registry), [first])
        self.assertEqual(set(self.registry.parent.iterdir()), existing_files)

    def test_fsync_failure_preserves_registry_and_cleans_temporary_files(self) -> None:
        first = self.register()
        saved = self.registry.read_bytes()
        existing_files = set(self.registry.parent.iterdir())
        self.pdf.write_bytes(synthetic_pdf("SYNTHETIC UPDATED REPORT"))
        with patch("metric_mirage.reports.os.fsync", side_effect=OSError("Sync failed")):
            with self.assertRaises((OSError, ValueError)):
                self.register(fiscal_period="FY2025")

        self.assertEqual(self.registry.read_bytes(), saved)
        self.assertEqual(load_registry(self.registry), [first])
        self.assertEqual(set(self.registry.parent.iterdir()), existing_files)

    def test_existing_lock_is_preserved_and_blocks_registration(self) -> None:
        self.register()
        saved = self.registry.read_bytes()
        lock = self.registry.with_name(self.registry.name + ".lock")
        lock.write_text("SYNTHETIC: another writer owns this lock", encoding="utf-8")
        lock_bytes = lock.read_bytes()
        existing_files = set(self.registry.parent.iterdir())
        self.pdf.write_bytes(synthetic_pdf("SYNTHETIC UPDATED REPORT"))

        with self.assertRaises((OSError, ValueError)):
            self.register(fiscal_period="FY2025")

        self.assertEqual(self.registry.read_bytes(), saved)
        self.assertEqual(lock.read_bytes(), lock_bytes)
        self.assertEqual(set(self.registry.parent.iterdir()), existing_files)

    def test_registry_path_cannot_replace_the_original_pdf(self) -> None:
        saved = self.pdf.read_bytes()

        with self.assertRaises((OSError, ValueError)):
            self.register(registry_path=self.pdf)

        self.assertEqual(self.pdf.read_bytes(), saved)
        self.assertEqual(list(self.directory.iterdir()), [self.pdf])


if __name__ == "__main__":
    unittest.main()
