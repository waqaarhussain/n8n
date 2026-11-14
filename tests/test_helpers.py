import importlib.util
from pathlib import Path


def test_helper_menu_number_continues_selected_flow(monkeypatch):
    path = Path(__file__).resolve().parents[1]/'scripts/helper-menu.py'
    spec = importlib.util.spec_from_file_location('helper_menu', path)
    menu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(menu)
    inputs = iter(['bad', '1'])
    monkeypatch.setattr('builtins.input', lambda _: next(inputs))
    assert menu.choose() == 'stats'
    monkeypatch.setattr('builtins.input', lambda _: '0')
    assert menu.choose() is None
    commands = {command for command, _ in menu.OPTIONS}
    assert {'stats', 'stats-reset', 'school-import', 'update', 'check-icloud'} <= commands


def test_shortcut_install_is_idempotent_and_preserves_other_commands(tmp_path):
    path = Path(__file__).resolve().parents[1]/'scripts/install-helper.py'
    spec = importlib.util.spec_from_file_location('install_helper', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path/'n8n-live'
    target.write_text('target')
    shortcut = tmp_path/'school-import'
    module.install_shortcut(shortcut, target, 'fallback')
    module.install_shortcut(shortcut, target, 'fallback')
    assert shortcut.resolve() == target
    shortcut.unlink()
    shortcut.write_text('another project')
    module.install_shortcut(shortcut, target, 'fallback')
    assert shortcut.read_text() == 'another project'
