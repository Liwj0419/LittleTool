import unittest
from unittest.mock import Mock, patch

from read_text.core import App


class OverlayControlsTest(unittest.TestCase):
    def setUp(self):
        self.app = App.__new__(App)
        self.app.overlay = Mock()
        self.app.overlay.visible = True
        self.app.overlay.hwnd.return_value = 123
        self.app.on_cancel = Mock()

    def test_cancel_does_nothing_without_a_verified_foreground_window(self):
        for hwnd, foreground in ((0, True), (123, False)):
            self.app.overlay.hwnd.return_value = hwnd
            with patch('read_text.core.screen.foreground_is', return_value=foreground):
                self.app._on_cancel_hotkey()
        self.app.on_cancel.assert_not_called()

    def test_cancel_works_for_the_foreground_overlay(self):
        self.app._cancel_spec = lambda: 'Esc'
        with patch('read_text.core.screen.foreground_is', return_value=True):
            self.app._on_cancel_hotkey()
        self.app.on_cancel.assert_called_once()

    def test_failed_or_empty_copy_keeps_the_overlay_open(self):
        with patch('read_text.core.screen.set_clipboard', return_value=False):
            self.assertFalse(self.app.on_copy('文字'))
            self.assertFalse(self.app.on_copy(''))
        self.app.overlay.hide.assert_not_called()

    def test_successful_copy_hides_without_waiting_on_the_renderer(self):
        with patch('read_text.core.screen.set_clipboard', return_value=True):
            self.assertTrue(self.app.on_copy('文字'))
        self.app.overlay.hide.assert_called_once_with(wait=False)


if __name__ == '__main__':
    unittest.main()
