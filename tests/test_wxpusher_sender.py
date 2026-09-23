import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from trendradar.notification.senders import send_to_wxpusher


class WxPusherSenderTests(unittest.TestCase):
    @patch("trendradar.notification.senders.requests.post")
    def test_standard_api_sends_to_uid_without_using_spt(self, post):
        response = MagicMock()
        response.json.return_value = {"code": 1000, "success": True, "msg": "ok"}
        post.return_value = response

        sent = send_to_wxpusher(
            spt="SPT_backup",
            report_data={},
            report_type="测试日报",
            split_content_func=lambda *args, **kwargs: ["digest content"],
            app_token="AT_test",
            uids=["UID_test"],
            batch_interval=0,
        )

        self.assertTrue(sent)
        self.assertEqual(
            post.call_args.args[0],
            "https://wxpusher.zjiecode.com/api/send/message",
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["appToken"], "AT_test")
        self.assertEqual(payload["uids"], ["UID_test"])
        self.assertNotIn("spt", payload)

    @patch("trendradar.notification.senders.requests.post")
    def test_incomplete_standard_config_does_not_fall_back_to_spt(self, post):
        output = io.StringIO()
        with redirect_stdout(output):
            sent = send_to_wxpusher(
                spt="SPT_backup",
                report_data={},
                report_type="测试日报",
                split_content_func=lambda *args, **kwargs: ["digest content"],
                app_token="AT_test",
                uids=[],
            )

        self.assertFalse(sent)
        post.assert_not_called()
        self.assertIn("需要同时配置 appToken 和 UID", output.getvalue())


if __name__ == "__main__":
    unittest.main()
