from __future__ import annotations

import os
import queue
import threading
import uuid
from unittest.mock import Mock, patch

import pytest

import desktop
from desktop import DesktopApp
from media_manager.single_instance import SingleInstance
from media_manager.tray import TrayController


@pytest.mark.skipif(os.name != "nt", reason="Windows named objects are required")
def test_named_mutex_rejects_second_instance_and_signals_restore():
    unique = uuid.uuid4().hex
    first = SingleInstance(
        mutex_name=rf"Local\HomeMediaManager.Test.{unique}",
        event_name=rf"Local\HomeMediaManager.TestRestore.{unique}",
    )
    second = SingleInstance(
        mutex_name=first.mutex_name,
        event_name=first.event_name,
    )
    restored = threading.Event()

    assert first.acquire() is True
    first.start_restore_monitor(restored.set)
    assert second.acquire() is False
    assert restored.wait(2)

    second.close()
    first.close()
    first.close()

    replacement = SingleInstance(
        mutex_name=first.mutex_name,
        event_name=first.event_name,
    )
    assert replacement.acquire() is True
    replacement.close()


def test_second_desktop_launch_exits_before_tk_or_servers_are_created():
    instance = Mock()
    instance.acquire.return_value = False
    with patch("desktop.SingleInstance", return_value=instance), patch("desktop.tk.Tk") as root_factory:
        desktop.main()
    root_factory.assert_not_called()
    instance.acquire.assert_called_once_with()


def test_first_run_wizard_precedes_desktop_and_cancel_starts_no_server():
    instance = Mock()
    instance.acquire.return_value = True
    root = Mock()
    repository = Mock()
    repository.is_configured = False
    with (
        patch("desktop.SingleInstance", return_value=instance),
        patch("desktop.tk.Tk", return_value=root),
        patch("desktop.ConfigRepository", return_value=repository),
        patch("desktop.run_setup_dialog", return_value=False) as setup,
        patch("desktop.DesktopApp") as desktop_factory,
    ):
        desktop.main()

    repository.migrate_legacy.assert_called_once_with()
    setup.assert_called_once_with(root, repository, first_run=True)
    desktop_factory.assert_not_called()
    root.destroy.assert_called_once_with()
    instance.close.assert_called_once_with()


def test_existing_configuration_skips_first_run_wizard():
    instance = Mock()
    instance.acquire.return_value = True
    root = Mock()
    repository = Mock()
    repository.is_configured = True
    desktop_app = Mock()
    with (
        patch("desktop.SingleInstance", return_value=instance),
        patch("desktop.tk.Tk", return_value=root),
        patch("desktop.ConfigRepository", return_value=repository),
        patch("desktop.run_setup_dialog") as setup,
        patch("desktop.DesktopApp", return_value=desktop_app) as desktop_factory,
    ):
        desktop.main()

    setup.assert_not_called()
    repository.load.assert_called_once_with()
    desktop_factory.assert_called_once_with(root, instance, repository)
    desktop_app.close.assert_called_once_with()


def test_settings_button_opens_editor_and_explains_restart():
    app = DesktopApp.__new__(DesktopApp)
    app.root = Mock()
    app.config_repository = Mock()
    with (
        patch("desktop.run_setup_dialog", return_value=True) as setup,
        patch("desktop.messagebox.showinfo") as info,
    ):
        app.open_settings()

    setup.assert_called_once_with(app.root, app.config_repository, first_run=False)
    info.assert_called_once()


def bare_desktop() -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)
    app.root = Mock()
    app.server = Mock()
    app.tray = Mock()
    app.instance = Mock()
    app.ui_actions = queue.Queue()
    app._closing = False
    app._closed = False
    return app


def test_window_close_hides_to_tray_without_stopping_servers():
    app = bare_desktop()
    app.hide_to_tray()
    app.root.withdraw.assert_called_once_with()
    app.server.stop.assert_not_called()
    assert app._closed is False


def test_tray_click_and_show_action_restore_on_tk_thread():
    app = bare_desktop()
    tray = TrayController(
        lambda: app.request_ui_action("show"),
        lambda: app.request_ui_action("exit"),
    )
    tray._show()
    with patch("desktop.bring_window_to_foreground") as foreground:
        app._drain_ui_actions()
    app.root.deiconify.assert_called_once_with()
    app.root.state.assert_called_once_with("normal")
    app.root.lift.assert_called_once_with()
    app.root.focus_force.assert_called_once_with()
    foreground.assert_called_once_with(app.root.winfo_id.return_value)


def test_tray_menu_exposes_default_show_and_exit_commands():
    with patch("media_manager.tray.pystray.Icon") as icon_factory:
        icon = icon_factory.return_value
        controller = TrayController(Mock(), Mock())
        controller.start()
        controller._thread.join(timeout=2)
        menu = icon_factory.call_args.args[3]

        assert [(item.text, item.default) for item in menu.items] == [
            ("Show Home Media Manager", True),
            ("Exit", False),
        ]
        controller.stop()
        controller.stop()
        icon.stop.assert_called_once_with()


def test_tray_exit_stops_everything_and_is_idempotent():
    app = bare_desktop()
    tray_callbacks = TrayController(
        lambda: app.request_ui_action("show"),
        lambda: app.request_ui_action("exit"),
    )
    tray_callbacks._exit()
    app._drain_ui_actions()
    app.close()

    app.server.stop.assert_called_once_with()
    app.tray.stop.assert_called_once_with()
    app.instance.close.assert_called_once_with()
    app.root.quit.assert_called_once_with()
    app.root.destroy.assert_called_once_with()
    assert app._closed is True
