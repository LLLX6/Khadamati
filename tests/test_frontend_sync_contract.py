import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSyncContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "index.html").read_text(encoding="utf-8")

    def function_body(self, name):
        match = re.search(
            rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\s*\{{(.*?)\n\}}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_account_and_chat_polling_share_one_coordinator(self):
        self.assertIn("const ACCOUNT_SYNC =", self.source)
        self.assertIn("ACCOUNT_REFRESH_INTERVAL_MS = 15000", self.source)
        self.assertIn("CHAT_REFRESH_INTERVAL_MS = 10000", self.source)
        chat_refresh = self.function_body("refreshOpenRequestChat")
        self.assertIn("coordinatedAccountSync", chat_refresh)
        self.assertNotIn("api('/api/bootstrap'", chat_refresh)
        start = self.source.index("async function coordinatedAccountSync")
        end = self.source.index("async function refreshActiveAccountData", start)
        coordinator = self.source[start:end]
        self.assertIn("AbortController", coordinator)
        self.assertIn("stale:true", coordinator)
        self.assertIn("lastCompletedContext===context.key", coordinator)
        self.assertIn("if(changed)queueMicrotask(renderActionPrompt)", coordinator)
        self.assertNotIn("finally{queueMicrotask(renderActionPrompt)", coordinator)

    def test_notification_state_is_monotonic(self):
        merge = self.function_body("mergeNotificationState")
        self.assertIn("Math.max", merge)
        self.assertIn("old.read||next.read", merge)
        self.assertIn("readAt", merge)
        self.assertIn("actedAt", merge)
        self.assertIn("persistNotificationReads", self.source)

    def test_pending_requests_survive_remote_reconciliation(self):
        reconcile = self.function_body("reconcileCustomerRequests")
        self.assertIn("requestHasPendingSync", reconcile)
        self.assertIn("rows.push(localRequest)", reconcile)
        merge_remote = self.function_body("mergeRemote")
        self.assertIn("reconcileCustomerRequests(data.customerRequests)", merge_remote)

    def test_english_instruction_fallbacks_are_present(self):
        self.assertIn("const ASSISTANT_RULE_EN=", self.source)
        self.assertIn("computer laptop printer", self.source)
        self.assertIn("const NOTIFICATION_ACTION_KIND_EN=", self.source)
        self.assertIn("rebuildLocalizedInstructionSurface", self.source)


if __name__ == "__main__":
    unittest.main()
