"""Install project shortcuts without replacing another project's commands."""
from pathlib import Path


def install_shortcut(shortcut, target, fallback):
    if shortcut.is_symlink() and shortcut.resolve() == target:
        return
    if shortcut.exists() or shortcut.is_symlink():
        print(f'Existing {shortcut.name} command kept. Use: {fallback}')
        return
    shortcut.symlink_to(target)
    print(f'Installed {shortcut.name}. Run: {shortcut.name}')


def main():
    target = Path('/usr/local/bin/n8n-live')
    install_shortcut(Path('/usr/local/bin/helper'), target, 'sudo n8n-live helper')
    install_shortcut(Path('/usr/local/bin/school-import'), target, 'sudo n8n-live school-import')


if __name__ == '__main__':
    main()
