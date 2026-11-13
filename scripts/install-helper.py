"""Install the requested shortcut without replacing another project's helper."""
from pathlib import Path


def main():
    shortcut = Path('/usr/local/bin/helper')
    target = Path('/usr/local/bin/n8n-live')
    if shortcut.is_symlink() and shortcut.resolve() == target:
        return
    if shortcut.exists() or shortcut.is_symlink():
        print('Existing helper command kept. Open the n8n menu with: sudo n8n-live helper')
        return
    shortcut.symlink_to(target)
    print('Installed helper. Run: helper')


if __name__ == '__main__':
    main()
