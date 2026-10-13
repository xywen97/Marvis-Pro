"""Desktop task notifications; monitoring continues while the window is hidden."""
from __future__ import annotations

import json
import logging
import threading

log = logging.getLogger(__name__)
TITLES = {'completed': '任务已完成', 'failed': '任务失败', 'cancelled': '任务已停止',
          'interrupted': '任务已中断', 'waiting_approval': '需要你的授权'}


class TaskNotifications:
    def __init__(self):
        self.seen = {}
        self.initialized = False

    def update(self, tasks):
        notices = []
        for task in tasks:
            status = task['status']
            approval = next((e['id'] for e in reversed(task.get('events', [])) if e['kind'] == 'approval'), None)
            key = (status, approval if status == 'waiting_approval' else None)
            previous = self.seen.get(task['id'])
            approval_details_arrived = status == 'waiting_approval' and previous == (status, None)
            if key != previous and not approval_details_arrived and status in TITLES and (self.initialized or status == 'waiting_approval'):
                notices.append(task)
            self.seen[task['id']] = key
        self.initialized = True
        return notices


class DesktopNotifications:
    def __init__(self, runtime, window):
        self.runtime, self.window = runtime, window
        self.stop = threading.Event()
        self.center = self.delegate = None
        self.permission = None
        self.pending = {}
        self.lock = threading.Lock()
        self.tracker = TaskNotifications()
        self.initial_notices = self.tracker.update(runtime.snapshot()['tasks'])
        self.thread = None

    def start(self):
        # UNUserNotificationCenter aborts an unbundled command-line process.
        # Keep the in-app reminders available for `python main.py --app`.
        from Foundation import NSBundle, NSObject
        if NSBundle.mainBundle().bundleIdentifier():
            try:
                import objc
                import UserNotifications as UN
                host = self

                class MarvisNotificationDelegate(NSObject, protocols=[objc.protocolNamed('UNUserNotificationCenterDelegate')]):
                    def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notice, completion):
                        completion(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList | UN.UNNotificationPresentationOptionSound)

                    def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, completion):
                        try:
                            if response.actionIdentifier() == UN.UNNotificationDefaultActionIdentifier:
                                host.activate(str(response.notification().request().content().userInfo()['task_id']))
                        finally:
                            completion()

                self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
                self.delegate = MarvisNotificationDelegate.alloc().init()
                self.center.setDelegate_(self.delegate)
                self.center.requestAuthorizationWithOptions_completionHandler_(
                    UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound,
                    self._authorization)
            except Exception:
                log.exception('无法初始化系统通知；应用内提醒仍可用')
        for task in self.initial_notices:
            self.deliver(task)
        self.thread = threading.Thread(target=self._watch, daemon=True, name='marvis-notifications')
        self.thread.start()

    def activate(self, task_id):
        from AppKit import NSApplication
        from PyObjCTools import AppHelper

        def focus():
            self.window.show()
            self.window.restore()
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        AppHelper.callAfter(focus)
        # evaluate_js waits for WKWebView's main loop, so run it off that loop.
        threading.Thread(target=self.window.evaluate_js,
                         args=('window.MarvisNotifications.openTask(' + json.dumps(task_id) + ')',),
                         daemon=True).start()

    def deliver(self, task):
        if self.center is None:
            return
        with self.lock:
            if self.permission is None:
                self.pending[task['id']] = task
                return
            if not self.permission:
                return
        import UserNotifications as UN
        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_('MarvisXY · ' + TITLES[task['status']])
        content.setBody_(task['prompt'][:100] + '\n点击打开对应对话')
        content.setSound_(UN.UNNotificationSound.defaultSound())
        content.setUserInfo_({'task_id': task['id']})
        content.setThreadIdentifier_(task['session_id'])
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(task['id'], content, None)
        self.center.addNotificationRequest_withCompletionHandler_(request,
            lambda error: log.warning('发送通知失败: %s', error) if error else None)

    def _authorization(self, granted, error):
        with self.lock:
            self.permission = bool(granted)
            pending = list(self.pending.values())
            self.pending.clear()
        if not granted:
            log.warning('系统通知未获授权: %s', error or '用户未允许')
            return
        for task in pending:
            self.deliver(task)

    def _watch(self):
        while not self.stop.wait(.5):
            try:
                for task in self.tracker.update(self.runtime.snapshot()['tasks']):
                    self.deliver(task)
            except Exception:
                log.exception('检查任务通知失败')

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
