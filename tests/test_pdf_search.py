"""Search only invented, temporary PDFs, including real Unicode text extraction."""

from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
import io
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfReader, PdfWriter

from metric_mirage import SearchMatch, SearchResult, register_report, search_report


def synthetic_unicode_pdf(pages: list[str]) -> bytes:
    """Build a synthetic PDF with a ToUnicode font for offline extraction tests.

    The fixture has an explicit CID-to-Unicode map so pypdf can extract English
    and CJK without installed fonts or another PDF generation dependency. It is
    an extraction fixture; the font is not embedded for visual rendering.
    """
    characters = sorted(set("".join(pages)) - {"\n"})
    codes = {character: index for index, character in enumerate(characters, 1)}
    mapping = [
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n",
        b"/CMapName /SyntheticUnicode def\n/CMapType 2 def\n",
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n",
    ]
    for start in range(0, len(characters), 100):
        batch = characters[start:start + 100]
        mapping.append(f"{len(batch)} beginbfchar\n".encode("ascii"))
        for character in batch:
            target = character.encode("utf-16-be").hex().upper()
            mapping.append(f"<{codes[character]:04X}> <{target}>\n".encode("ascii"))
        mapping.append(b"endbfchar\n")
    mapping.append(b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    cmap = b"".join(mapping)
    page_ids = [6 + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{number} 0 R" for number in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /SyntheticUnicode "
        b"/Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SyntheticUnicode "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/CIDToGIDMap /Identity /DW 600 >>",
        b"<< /Length " + str(len(cmap)).encode("ascii") + b" >>\nstream\n"
        + cmap + b"endstream",
    ]
    for index, text in enumerate(pages):
        content = bytearray(b"BT /F1 12 Tf 30 260 Td\n")
        for line_index, line in enumerate(text.split("\n")):
            if line_index:
                content.extend(b"0 -16 Td\n")
            encoded = "".join(f"{codes[character]:04X}" for character in line)
            content.extend(f"<{encoded}> Tj\n".encode("ascii"))
        content.extend(b"ET\n")
        objects.extend([
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + f"{7 + 2 * index} 0 R >>".encode("ascii"),
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
            + content + b"endstream",
        ])
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(document)


def encrypted_synthetic_pdf(password: str) -> bytes:
    """Encrypt invented text using pypdf's standard PDF writer."""
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(io.BytesIO(
        synthetic_unicode_pdf(["SYNTHETIC Adjusted EBITDA"])
    )))
    writer.encrypt(password)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class PdfSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage PDF search ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC report.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.metadata = {
            "ticker": "0700.HK", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented PDF search fixture",
        }

    def register(self, pages: list[str], **changes: object):
        self.pdf.write_bytes(synthetic_unicode_pdf(pages))
        arguments: dict[str, object] = dict(self.metadata, registry_path=self.registry)
        arguments.update(changes)
        return register_report(self.pdf, **arguments)  # type: ignore[arg-type]

    def search(self, keyword: str = "Adjusted EBITDA", **changes: object) -> SearchResult:
        arguments: dict[str, object] = {
            "fiscal_period": "FY2024", "keyword": keyword,
            "registry_path": self.registry,
        }
        arguments.update(changes)
        return search_report("00700", **arguments)  # type: ignore[arg-type]

    def mock_pages(self, *texts: str | None):
        return patch("metric_mirage.search.PdfReader", return_value=SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=Mock(return_value=text)) for text in texts],
        ))

    def test_actual_pdf_text_extraction_preserves_original_case(self) -> None:
        record = self.register(["SYNTHETIC financial report: Adjusted EBITDA improved."])
        result = self.search()

        self.assertIsInstance(result, SearchResult)
        self.assertEqual(result.report, record)
        self.assertEqual(result.keyword, "Adjusted EBITDA")
        self.assertEqual(result.total_matches, 1)
        self.assertEqual(len(result.matches), 1)
        self.assertIsInstance(result.matches[0], SearchMatch)
        self.assertEqual(result.matches[0].page_number, 1)
        self.assertIn("Adjusted EBITDA", result.matches[0].excerpt)

    def test_actual_pdf_english_search_is_case_insensitive(self) -> None:
        self.register(["SYNTHETIC ADJUSTED EBITDA, adjusted ebitda, Adjusted EBITDA"])
        result = self.search("aDjUsTeD eBiTdA")

        self.assertEqual(result.total_matches, 3)
        self.assertEqual([match.page_number for match in result.matches], [1, 1, 1])
        self.assertTrue(all(match.keyword == "aDjUsTeD eBiTdA" for match in result.matches))

    def test_actual_pdf_traditional_chinese_search(self) -> None:
        self.register(["SYNTHETIC 合成財務報告：經調整淨利潤有所變動。"])
        result = self.search("經調整淨利潤")

        self.assertEqual(result.total_matches, 1)
        self.assertIn("經調整淨利潤", result.matches[0].excerpt)

    def test_actual_pdf_simplified_chinese_search(self) -> None:
        self.register(["SYNTHETIC 合成财务报告：经调整净利润有所变动。"])
        result = self.search("经调整净利润")

        self.assertEqual(result.total_matches, 1)
        self.assertIn("经调整净利润", result.matches[0].excerpt)

    def test_traditional_and_simplified_are_not_implicitly_converted(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤"])
        self.assertEqual(self.search("经调整净利润").total_matches, 0)

    def test_pdf_page_numbers_include_blank_pages(self) -> None:
        self.register(["", "SYNTHETIC Adjusted EBITDA", "", "Adjusted EBITDA final"])
        result = self.search()

        self.assertEqual([match.page_number for match in result.matches], [2, 4])
        self.assertEqual(result.total_matches, 2)

    def test_results_follow_page_and_within_page_order(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        first = "FIRST " + "x" * 180 + " Adjusted EBITDA " + "x" * 180
        second = "SECOND " + "y" * 180 + " Adjusted EBITDA " + "y" * 180
        with self.mock_pages(first + second, "THIRD Adjusted EBITDA"):
            result = self.search()

        self.assertEqual([match.page_number for match in result.matches], [1, 1, 2])
        self.assertIn("x", result.matches[0].excerpt)
        self.assertNotIn("y", result.matches[0].excerpt)
        self.assertIn("y", result.matches[1].excerpt)
        self.assertIn("THIRD", result.matches[2].excerpt)

    def test_max_results_limits_output_but_counts_whole_document(self) -> None:
        self.register(["Adjusted EBITDA; adjusted ebitda", "ADJUSTED EBITDA", "Adjusted EBITDA"])
        result = self.search(max_results=2)

        self.assertEqual(result.total_matches, 4)
        self.assertEqual(len(result.matches), 2)
        self.assertEqual([match.page_number for match in result.matches], [1, 1])

    def test_default_max_results_is_ten(self) -> None:
        self.register(["; ".join(["Adjusted EBITDA"] * 12)])
        result = self.search()

        self.assertEqual(result.total_matches, 12)
        self.assertEqual(len(result.matches), 10)

    def test_repeated_search_is_deterministic(self) -> None:
        self.register(["Adjusted EBITDA, adjusted EBITDA", "SYNTHETIC 經調整淨利潤"])
        self.assertEqual(self.search(), self.search())

    def test_normalized_whitespace_search_returns_original_excerpt(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        original = "Before: Adjusted\n \tEBITDA. After."
        with self.mock_pages(original):
            result = self.search("  adjusted\t EBITDA  ")

        self.assertEqual(result.total_matches, 1)
        self.assertEqual(result.matches[0].excerpt, original)

    def test_unicode_casefold_expansion_keeps_original_offsets(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        raw = "ß" * 220 + " Stra\nße " + "尾" * 220
        with self.mock_pages(raw):
            result = self.search("stra sse")

        self.assertEqual(result.total_matches, 1)
        self.assertIn("Stra\nße", result.matches[0].excerpt)
        self.assertLessEqual(len(result.matches[0].excerpt), 300)
        self.assertIn(result.matches[0].excerpt, raw)

    def test_excerpts_are_bounded_original_text_fragments(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        original = "a" * 600 + " Adjusted EBITDA " + "b" * 600
        with self.mock_pages(original):
            result = self.search()

        self.assertLessEqual(len(result.matches[0].excerpt), 300)
        self.assertIn("Adjusted EBITDA", result.matches[0].excerpt)
        self.assertIn(result.matches[0].excerpt, original)

    def test_long_keyword_can_match_with_bounded_excerpt(self) -> None:
        keyword = "SYNTHETIC" * 50
        self.register([keyword])
        result = self.search(keyword)

        self.assertEqual(result.total_matches, 1)
        self.assertLessEqual(len(result.matches[0].excerpt), 300)

    def test_literal_matching_has_no_semantic_or_regex_expansion(self) -> None:
        self.register(["SYNTHETIC (Adjusted EBITDA) and Adjusted Net Profit"])
        self.assertEqual(self.search("(Adjusted EBITDA)").total_matches, 1)
        self.assertEqual(self.search("Adjusted.*EBITDA").total_matches, 0)
        self.assertEqual(self.search("EBITDA adjusted").total_matches, 0)

    def test_matching_does_not_cross_pdf_page_boundaries(self) -> None:
        self.register(["SYNTHETIC Adjusted", "EBITDA"])
        self.assertEqual(self.search().total_matches, 0)

    def test_matches_do_not_overlap(self) -> None:
        self.register(["SYNTHETIC aaa"])
        self.assertEqual(self.search("aa").total_matches, 1)

    def test_no_matches_is_a_normal_empty_result(self) -> None:
        self.register(["SYNTHETIC ordinary report text"])
        result = self.search()

        self.assertEqual(result.total_matches, 0)
        self.assertEqual(result.matches, ())

    def test_search_match_is_immutable(self) -> None:
        match = SearchMatch(page_number=1, keyword="EBITDA", excerpt="SYNTHETIC EBITDA")
        with self.assertRaises(FrozenInstanceError):
            match.page_number = 2  # type: ignore[misc]

    def test_invalid_max_results_are_rejected(self) -> None:
        self.register(["Adjusted EBITDA"])
        for value in (0, -1, 1001, True, False, 1.5, "2", None):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)) as raised:
                    self.search(max_results=value)
                self.assertIn("max_results", str(raised.exception))

    def test_invalid_keywords_are_rejected(self) -> None:
        self.register(["Adjusted EBITDA"])
        for keyword in ("", " \t\n", None, 42):
            with self.subTest(keyword=keyword):
                with self.assertRaises((TypeError, ValueError)) as raised:
                    self.search(keyword)  # type: ignore[arg-type]
                self.assertIn("keyword", str(raised.exception))

    def test_invalid_period_or_type_is_rejected(self) -> None:
        self.register(["Adjusted EBITDA"])
        for changes in ({"fiscal_period": " "}, {"report_type": "quarterly"}):
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    self.search(**changes)

    def test_missing_registry_reports_no_report_without_creating_data(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self.search()
        self.assertIn("00700", str(raised.exception))
        self.assertIn("FY2024", str(raised.exception))
        self.assertFalse(self.registry.exists())

    def test_report_selection_requires_requested_ticker_and_period(self) -> None:
        self.register(["Adjusted EBITDA"])
        with self.assertRaises(ValueError):
            self.search(fiscal_period="FY2025")
        with self.assertRaises(ValueError):
            search_report("1810", fiscal_period="FY2024", keyword="Adjusted EBITDA",
                          registry_path=self.registry)

    def test_report_selection_strips_period_without_case_inference(self) -> None:
        self.register(["Adjusted EBITDA"])
        self.assertEqual(self.search(fiscal_period=" FY2024 ").total_matches, 1)
        with self.assertRaises(ValueError):
            self.search(fiscal_period="fy2024")

    def test_multiple_reports_are_ambiguous_and_type_can_resolve(self) -> None:
        annual = self.register(["SYNTHETIC annual Adjusted EBITDA"])
        interim = self.directory / "SYNTHETIC interim.pdf"
        interim.write_bytes(synthetic_unicode_pdf(["SYNTHETIC interim Adjusted EBITDA"]))
        register_report(interim, **dict(self.metadata, report_type="interim"),
                        registry_path=self.registry)

        with self.assertRaises(ValueError) as raised:
            self.search()
        self.assertIn("reports list", str(raised.exception))
        self.assertEqual(self.search(report_type="annual").report, annual)

    def test_type_filter_still_rejects_multiple_reports_of_same_type(self) -> None:
        self.register(["SYNTHETIC original annual Adjusted EBITDA"])
        other = self.directory / "SYNTHETIC other annual.pdf"
        other.write_bytes(synthetic_unicode_pdf(["SYNTHETIC revised annual Adjusted EBITDA"]))
        register_report(other, **self.metadata, registry_path=self.registry)

        with self.assertRaises(ValueError) as raised:
            self.search(report_type="annual")
        self.assertIn("reports list", str(raised.exception))

    def test_original_pdf_missing_is_a_clear_error(self) -> None:
        self.register(["Adjusted EBITDA"])
        self.pdf.unlink()
        with self.assertRaises(ValueError) as raised:
            self.search()
        self.assertIn(str(self.pdf), str(raised.exception))

    def test_changed_pdf_is_rejected_before_reader_is_used(self) -> None:
        self.register(["SYNTHETIC original Adjusted EBITDA"])
        self.pdf.write_bytes(synthetic_unicode_pdf(["SYNTHETIC changed Adjusted EBITDA"]))
        with patch("metric_mirage.search.PdfReader") as reader:
            with self.assertRaises(ValueError) as raised:
                self.search()
        self.assertRegex(str(raised.exception).lower(), r"changed|sha256|checksum")
        reader.assert_not_called()

    def test_unreadable_original_pdf_is_a_clear_error(self) -> None:
        self.register(["Adjusted EBITDA"])
        real_open = Path.open

        def fail_pdf_open(path: Path, *args: object, **kwargs: object):
            if path == self.pdf:
                raise PermissionError("SYNTHETIC read denied")
            return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

        with patch("pathlib.Path.open", new=fail_pdf_open):
            with self.assertRaises((OSError, ValueError)) as raised:
                self.search()
        self.assertIn("read denied", str(raised.exception))

    def test_registered_corrupt_pdf_reports_parse_failure(self) -> None:
        self.pdf.write_bytes(b"%PDF-1.4\nSYNTHETIC corrupt objects\n%%EOF\n")
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        with self.assertRaises(ValueError) as raised:
            self.search()
        self.assertIn("PDF", str(raised.exception))

    def test_actual_pdf_without_text_is_distinct_from_no_matches(self) -> None:
        self.register(["", ""])
        with self.assertRaises(ValueError) as raised:
            self.search()
        self.assertRegex(str(raised.exception).lower(), r"extractable text|text layer|no text")

    def test_none_and_whitespace_page_text_are_not_searchable_text(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        with self.mock_pages(None, " \n\t"):
            with self.assertRaises(ValueError):
                self.search()

    def test_page_extraction_failure_has_page_context(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        reader = SimpleNamespace(is_encrypted=False, pages=[
            SimpleNamespace(extract_text=Mock(return_value="Adjusted EBITDA")),
            SimpleNamespace(extract_text=Mock(side_effect=RuntimeError("SYNTHETIC font failure"))),
        ])
        with patch("metric_mirage.search.PdfReader", return_value=reader):
            with self.assertRaises(ValueError) as raised:
                self.search(max_results=1)
        self.assertRegex(str(raised.exception).lower(), r"page\s+2")
        self.assertIn("font failure", str(raised.exception))

    def test_parser_warning_cannot_leak_to_stdout_and_logger_state_is_restored(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        logger = logging.getLogger("pypdf")
        original = (logger.handlers[:], logger.level, logger.propagate, logger.disabled)
        stdout = io.StringIO()
        handler = logging.StreamHandler(stdout)
        logger.addHandler(handler)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = True
        logger.disabled = True
        configured = (logger.handlers[:], logger.level, logger.propagate, logger.disabled)

        def warn_reader(*args: object, **kwargs: object):
            logger.warning("SYNTHETIC unsupported text font")
            return SimpleNamespace(is_encrypted=False, pages=[
                SimpleNamespace(extract_text=Mock(return_value="Adjusted EBITDA")),
            ])

        try:
            with patch("metric_mirage.search.PdfReader", side_effect=warn_reader), redirect_stdout(stdout):
                with self.assertRaises(ValueError) as raised:
                    self.search()
            self.assertIn("unsupported text font", str(raised.exception))
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                (logger.handlers, logger.level, logger.propagate, logger.disabled), configured,
            )
        finally:
            logger.handlers, logger.level, logger.propagate, logger.disabled = original
            handler.close()

    def test_password_encrypted_pdf_is_a_clear_error(self) -> None:
        self.pdf.write_bytes(encrypted_synthetic_pdf("SYNTHETIC secret"))
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        with self.assertRaises(ValueError) as raised:
            self.search()
        self.assertIn("encrypt", str(raised.exception).lower())

    def test_empty_password_encrypted_pdf_can_be_searched(self) -> None:
        self.pdf.write_bytes(encrypted_synthetic_pdf(""))
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        self.assertEqual(self.search().total_matches, 1)


if __name__ == "__main__":
    unittest.main()
