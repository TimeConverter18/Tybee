from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.recover_telegram_support import load_recovery_refs


class TelegramRecoveryTestCase(unittest.TestCase):
    def test_latest_copies_map_messages_to_clients_and_topics(self) -> None:
        null = chr(92) + "N"
        sql = (
            "COPY public.users (id, support_thread_id) FROM stdin;\n"
            f"1\t{null}\n\\.\n"
            "COPY public.users (id, support_thread_id) FROM stdin;\n"
            "1\t101\n2\t102\n\\.\n"
            "COPY public.relay_map (support_message_id, user_id) FROM stdin;\n"
            "20\t2\n10\t1\n\\.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sql"
            path.write_text(sql, encoding="utf-8")
            refs = load_recovery_refs(path)
        self.assertEqual([ref.support_message_id for ref in refs], [10, 20])
        self.assertEqual(refs[0].user_id, 1)
        self.assertEqual(refs[0].topic_id, 101)
        self.assertEqual(refs[1].user_id, 2)
        self.assertEqual(refs[1].topic_id, 102)

    def test_missing_topic_is_rejected(self) -> None:
        null = chr(92) + "N"
        sql = (
            "COPY public.users (id, support_thread_id) FROM stdin;\n"
            f"1\t{null}\n\\.\n"
            "COPY public.relay_map (support_message_id, user_id) FROM stdin;\n"
            "10\t1\n\\.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sql"
            path.write_text(sql, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "has no client topic"):
                load_recovery_refs(path)


if __name__ == "__main__":
    unittest.main()
