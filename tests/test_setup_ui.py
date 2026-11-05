from unittest.mock import Mock

from media_manager.setup_ui import SetupDialog


def make_dialog(parent_visible: bool) -> SetupDialog:
    dialog = SetupDialog.__new__(SetupDialog)
    dialog.parent = Mock()
    dialog.parent.winfo_viewable.return_value = parent_visible
    dialog.window = Mock()
    dialog.window.winfo_width.return_value = 650
    dialog.window.winfo_height.return_value = 560
    dialog.window.winfo_screenwidth.return_value = 1920
    dialog.window.winfo_screenheight.return_value = 1080
    return dialog


def test_first_run_dialog_is_mapped_without_transient_hidden_parent():
    dialog = make_dialog(parent_visible=False)

    dialog._show()

    dialog.window.transient.assert_not_called()
    dialog.window.geometry.assert_called_once_with("+635+260")
    dialog.window.deiconify.assert_called_once_with()
    dialog.window.lift.assert_called_once_with()
    dialog.window.attributes.assert_called_once_with("-topmost", True)

    finish_showing = dialog.window.after_idle.call_args.args[0]
    finish_showing()
    dialog.window.attributes.assert_called_with("-topmost", False)
    dialog.window.focus_force.assert_called_once_with()


def test_settings_dialog_remains_transient_to_visible_main_window():
    dialog = make_dialog(parent_visible=True)

    dialog._show()

    dialog.window.transient.assert_called_once_with(dialog.parent)
