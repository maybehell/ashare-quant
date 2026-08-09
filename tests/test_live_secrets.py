from pathlib import Path
import unittest

from ashare_quant.live_secrets import read_live_secrets, write_live_secrets


class LiveSecretsTests(unittest.TestCase):
    def test_dpapi_round_trip_does_not_store_plaintext(self):
        path = Path("test_outputs") / self._testMethodName / "credentials.dpapi"
        payload = {"smtp": {"password": "not-plaintext-secret"}, "huatai": {"account_id": "12345678"}}
        write_live_secrets(path, payload)
        self.assertEqual(read_live_secrets(path), payload)
        raw = path.read_bytes()
        self.assertNotIn(b"not-plaintext-secret", raw)
        self.assertNotIn(b"12345678", raw)


if __name__ == "__main__":
    unittest.main()
