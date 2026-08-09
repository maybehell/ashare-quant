from pathlib import Path
import unittest
from unittest.mock import patch

from ashare_quant.io_utils import replace_with_retry


class AtomicWriteTests(unittest.TestCase):
    def test_replace_with_retry_recovers_from_temporary_permission_error(self):
        calls = []

        def fake_replace(self, target):
            calls.append((self, target))
            if len(calls) == 1:
                raise PermissionError("temporary lock")

        with patch.object(Path, "replace", fake_replace), patch("ashare_quant.io_utils.time.sleep") as sleep:
            replace_with_retry(Path("tmp.csv"), Path("target.csv"), attempts=2, delay_seconds=0.01)

        self.assertEqual(len(calls), 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
