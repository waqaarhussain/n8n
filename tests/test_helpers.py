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
    assert {'stats', 'stats-reset', 'import', 'update', 'check-icloud'} <= commands
