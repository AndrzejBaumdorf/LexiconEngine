"""Mac のクイズアプリを起動する。

    py src/main.py           # 起動
    py src/main.py --reset   # DB を空から作り直して起動 (元の DB はバックアップに残す)
"""
from desktop.app import main

if __name__ == "__main__":
    main()
