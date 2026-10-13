import unittest
import sys
from unittest.mock import Mock, patch

from marvis.notifications import DesktopNotifications, TaskNotifications


class NotificationTests(unittest.TestCase):
    def test_transitions_and_repeated_approvals(self):
        tracker = TaskNotifications()
        task = {'id': 'a', 'status': 'completed', 'events': []}
        self.assertEqual(tracker.update([task]), [])  # Don't replay history.
        task = {'id': 'b', 'status': 'running', 'events': []}
        self.assertEqual(tracker.update([task]), [])
        task.update(status='waiting_approval', events=[{'id': 1, 'kind': 'approval'}])
        self.assertEqual(len(tracker.update([task])), 1)
        self.assertEqual(tracker.update([task]), [])
        task['events'].append({'id': 2, 'kind': 'approval'})
        self.assertEqual(len(tracker.update([task])), 1)
        task['status'] = 'completed'
        self.assertEqual(len(tracker.update([task])), 1)
        self.assertEqual(tracker.update([task]), [])

    def test_pending_at_start_and_fast_new_task(self):
        tracker = TaskNotifications()
        self.assertEqual(len(tracker.update([{'id': 'a', 'status': 'waiting_approval'}])), 1)
        self.assertEqual(tracker.update([{'id': 'a', 'status': 'waiting_approval', 'events': [{'id': 1, 'kind': 'approval'}]}]), [])
        self.assertEqual(len(tracker.update([{'id': 'b', 'status': 'failed'}])), 1)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS notification bridge')
    def test_native_delivery_and_click_callback(self):
        import Foundation
        import UserNotifications as UN
        center = Mock()
        bundle = Mock()
        bundle.mainBundle.return_value.bundleIdentifier.return_value = 'local.marvisxy.desktop'
        center_class = Mock()
        center_class.currentNotificationCenter.return_value = center
        runtime = Mock()
        runtime.snapshot.return_value = {'tasks': []}
        host = DesktopNotifications(runtime, Mock())
        task = {'id': 'task-1', 'session_id': 'session-1', 'status': 'completed', 'prompt': '检查电脑'}
        with patch.object(Foundation, 'NSBundle', bundle), patch.object(UN, 'UNUserNotificationCenter', center_class):
            try:
                host.start()
                self.assertIsNotNone(host.delegate)
                host.deliver(task)
                center.addNotificationRequest_withCompletionHandler_.assert_not_called()
                callback = center.requestAuthorizationWithOptions_completionHandler_.call_args.args[1]
                callback(True, None)
                request = center.addNotificationRequest_withCompletionHandler_.call_args.args[0]
                self.assertEqual(request.content().userInfo()['task_id'], 'task-1')
                self.assertEqual(request.content().threadIdentifier(), 'session-1')
                self.assertIn('任务已完成', request.content().title())
                response = Mock()
                response.actionIdentifier.return_value = UN.UNNotificationDefaultActionIdentifier
                response.notification.return_value.request.return_value = request
                completion = Mock()
                with patch.object(host, 'activate') as activate:
                    host.delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(center, response, completion)
                    activate.assert_called_once_with('task-1')
                    completion.assert_called_once_with()
            finally:
                host.close()


if __name__ == '__main__':
    unittest.main()
