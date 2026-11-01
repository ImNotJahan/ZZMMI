from zzmmi.runtime.xxmi_legacy import ARMED_MARKERS, ERROR_MARKERS, XxmiInPrefix
from zzmmi.runtime.yaagl import YaaglRuntime

# Excerpt of a real, successful XXMI Launcher 2.4.9 start under Wine.
NORMAL_START = """\
root ERROR Failed to check `None` for being EGS installation: 'NoneType' object has no attribute 'parent'
Traceback (most recent call last):
  File "C:\\XXMI~TSU\\RESO~HNW\\Bin\\core\\packages\\model_importers\\zzmi_package.py", line 124, in override_launch_context
AttributeError: 'NoneType' object has no attribute 'parent'
core.event_manager DEBUG FIRED: ApplicationEvents.StatusUpdate(status=LocaleString('Waiting for user to manually launch the game process ZenlessZoneZero.exe with official launcher...
"""


def test_harmless_traceback_is_not_an_error(tmp_path):
    xxmi = XxmiInPrefix(YaaglRuntime(tmp_path))
    xxmi.log.parent.mkdir(parents=True)
    xxmi.log.write_text(NORMAL_START)
    assert xxmi.wait_for(0, ARMED_MARKERS, ERROR_MARKERS, timeout=1) == "ok"


def test_show_error_is_an_error(tmp_path):
    xxmi = XxmiInPrefix(YaaglRuntime(tmp_path))
    xxmi.log.parent.mkdir(parents=True)
    xxmi.log.write_text("core.event_manager DEBUG FIRED: ApplicationEvents.ShowError(...)\n")
    assert xxmi.wait_for(0, ARMED_MARKERS, ERROR_MARKERS, timeout=1) == "error"
