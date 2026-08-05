from __future__ import annotations

import unittest


class LiveIntegrationTests(unittest.TestCase):
    @unittest.skip(
        "Live model integration is user-run only. Use the documented stage-specific "
        "CLI command after an offline dry-run; never run it in the ordinary suite."
    )
    def test_live_integration_user_run_only(self) -> None:
        self.fail("This placeholder must never execute in the offline suite.")


if __name__ == "__main__":
    unittest.main()
