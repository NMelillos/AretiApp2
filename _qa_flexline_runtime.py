"""Operational OCR contracts; exact-source acceptance remains in region QA."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
import flexline_region_ocr as ocr
from fifth_third_scanned import LOCK
from _qa_flexline_regions import synthetic


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        ocr._CACHE.clear()
        self.page = SimpleNamespace(width=612, height=792, curves=[1], images=[],
            extract_text=lambda: '', to_image=lambda **_: SimpleNamespace(original=Image.new('RGB', (850, 1100))))
        self.document = SimpleNamespace(pages=[self.page])
        self.result = SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'engine': '7.0.0', 'modelHash': '5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747',
            'pages': list(synthetic().values())}))

    def extract(self):
        with patch('pdfplumber.open', return_value=nullcontext(self.document)):
            return ocr._extract(b'isolated fixture', '00548546519', 'WOKING WAY LLC')

    def test_one_bounded_worker_and_pinned_result(self):
        def run(command, **options):
            self.assertEqual(command[2], '--regions')
            self.assertEqual(len(command[3:]), 15)
            self.assertTrue(all(Path(path).is_file() for path in command[3:]))
            self.assertEqual(options['timeout'], 90)
            self.assertEqual(set(options['env']) - {'PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP'}, set())
            return self.result
        with patch.object(ocr.subprocess, 'run', side_effect=run) as worker:
            self.assertEqual(str(self.extract()['closing_balance']), '-98.00')
            self.assertEqual(worker.call_count, 1)

    def test_operational_failures_release_lock_and_do_not_leak_content(self):
        cases = [subprocess.TimeoutExpired('private-command', 90), OSError('private-source'),
            SimpleNamespace(returncode=1, stdout='', stderr='private-source'),
            SimpleNamespace(returncode=0, stdout='private-source', stderr=''),
            SimpleNamespace(returncode=0, stdout='{}', stderr='')]
        for case in cases:
            with self.subTest(case=type(case).__name__):
                kwargs = {'side_effect': case} if isinstance(case, Exception) else {'return_value': case}
                with patch.object(ocr.subprocess, 'run', **kwargs), self.assertLogs(ocr._LOG, 'WARNING') as logs:
                    with self.assertRaises(Exception): self.extract()
                self.assertNotIn('private-', '\n'.join(logs.output))
                self.assertTrue(LOCK.acquire(blocking=False)); LOCK.release()

    def test_financial_disagreement_still_rejected(self):
        data = json.loads(self.result.stdout)
        data['pages'][-2] = data['pages'][-2].replace('98.00', '98.01')
        self.result.stdout = json.dumps(data)
        with patch.object(ocr.subprocess, 'run', return_value=self.result):
            with self.assertRaises(ocr.FlexlineValidationError): self.extract()

    def test_native_text_never_starts_ocr(self):
        self.page.extract_text = lambda: 'native statement'
        with patch.object(ocr.subprocess, 'run') as worker:
            self.assertIsNone(self.extract()); worker.assert_not_called()

    def test_cold_then_cached_metadata_identical_without_raw_text(self):
        with patch('pdfplumber.open', return_value=nullcontext(self.document)), patch.object(ocr.subprocess, 'run', return_value=self.result) as worker:
            first = ocr.evidence(b'isolated fixture', '00548546519', 'WOKING WAY LLC')
            second = ocr.evidence(b'isolated fixture', '00548546519', 'WOKING WAY LLC')
            self.assertEqual(first, second); self.assertEqual(worker.call_count, 1)
            self.assertNotIn('pages', first)


if __name__ == '__main__':
    unittest.main()
