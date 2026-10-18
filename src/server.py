"""iPad などのブラウザ版 (web/) を配信し、Mac の DB と同期するサーバー。

    python src/server.py --setup-cert   # 最初に1回: HTTPS の証明書を作る (mkcert)
    python src/server.py                # 同期したいときに起動 → https://<Macの名前>.local:8443

ブラウザ版はオフラインで動くために HTTPS が必要。証明書がなければ http で起動する (Mac の localhost からの確認用)。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from database import LexiconDatabase

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
WEB_DIR = ROOT / "web"
DEFAULT_DB_PATH = ROOT / "data" / "lexicon.db"
CERT_DIR = ROOT / "data" / "certs"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"
DEFAULT_PORT = 8443
# ブラウザ版の Pyodide に読み込ませる Python ファイル (tkinter を使うものは含めない)
BROWSER_MODULES = ("cloze", "database", "english", "german", "korean", "languages", "quiz_types", "web_bridge")

app = Flask(__name__)
app.config["DB_PATH"] = DEFAULT_DB_PATH


def open_db() -> LexiconDatabase:
    # 要求ごとに開く (Flask は要求を別スレッドで処理するため、接続を共有しない)
    database = LexiconDatabase(app.config["DB_PATH"])
    database.initialize()
    return database


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(WEB_DIR, "icon-180.png", mimetype="image/png")


@app.get("/py/<name>.py")
def python_module(name: str):
    if name not in BROWSER_MODULES:
        abort(404)
    return send_from_directory(SRC_DIR, f"{name}.py", mimetype="text/x-python")


@app.get("/api/version")
def version():
    """ブラウザ版の Python ファイルと画面のファイルの版。変わっていればブラウザ側が読み込み直す"""
    digest = hashlib.sha256()
    for path in [*(SRC_DIR / f"{name}.py" for name in BROWSER_MODULES), *sorted(WEB_DIR.rglob("*"))]:
        if path.is_file():
            digest.update(path.read_bytes())
    return jsonify({"version": digest.hexdigest()[:16], "modules": list(BROWSER_MODULES)})


@app.post("/api/sync")
def sync():
    """ブラウザ版の回答履歴をすべて受け取って Mac の DB に足し、ブラウザ版にない Mac の回答履歴を返す"""
    payload = request.get_json(force=True)
    rows = payload.get("rows", [])
    database = open_db()
    try:
        added, skipped = database.merge_history(rows)
        client_uids = {row["uid"] for row in rows}
        missing = database.history_rows(exclude_uids=client_uids)
    finally:
        database.close()
    return jsonify({"added": added, "skipped": skipped, "rows": missing})


@app.get("/api/content")
def content():
    """単語データだけのDB (回答履歴なし)。ブラウザ版はこれで単語データを置き換える"""
    database = open_db()
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content.db"
            database.export_content(path)
            data = path.read_bytes()
    finally:
        database.close()
    return send_file(io.BytesIO(data), mimetype="application/x-sqlite3", max_age=0)


@app.get("/<path:filename>")
def static_file(filename: str):
    return send_from_directory(WEB_DIR, filename)


@app.after_request
def no_cache(response):
    # キャッシュはブラウザ版の service worker が管理するので、ブラウザ自体のキャッシュは使わせない
    response.headers["Cache-Control"] = "no-store"
    return response


def host_names() -> list[str]:
    names = ["localhost", "127.0.0.1"]
    try:
        local = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, check=True).stdout.strip()
        names.insert(0, f"{local}.local")
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            names.append(probe.getsockname()[0])
    except OSError:
        pass
    return names


def setup_cert() -> None:
    """mkcert でローカル認証局を Mac に入れ、このMac用の証明書を作る。iPad には認証局の rootCA.pem を入れる"""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    names = host_names()
    print("Mac にローカル認証局を登録します (パスワードを聞かれます)…")
    subprocess.run(["mkcert", "-install"], check=True)
    subprocess.run(["mkcert", "-cert-file", str(CERT_FILE), "-key-file", str(KEY_FILE), *names], check=True)
    ca_root = Path(subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True, check=True).stdout.strip())
    print(f"\n証明書を作りました: {', '.join(names)}")
    print(f"\niPad には {ca_root / 'rootCA.pem'} を AirDrop で送り、次の2つを行ってください:")
    print("  1. 設定 → 一般 → VPNとデバイス管理 → ダウンロード済みプロファイル をインストール")
    print("  2. 設定 → 一般 → 情報 → 証明書信頼設定 → mkcert の認証局をオン")
    subprocess.run(["open", "-R", str(ca_root / "rootCA.pem")])


def main() -> None:
    parser = argparse.ArgumentParser(description="LexiconEngine のブラウザ版の配信と同期")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="同期するDB")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--setup-cert", action="store_true", help="HTTPS の証明書を作る (最初に1回)")
    args = parser.parse_args()
    if args.setup_cert:
        setup_cert()
        return
    app.config["DB_PATH"] = args.db
    open_db().close()
    secure = CERT_FILE.exists() and KEY_FILE.exists()
    scheme = "https" if secure else "http"
    if not secure:
        print("証明書がないので http で起動します (iPad でオフラインで使うには --setup-cert で証明書を作ってください)", file=sys.stderr)
    # 名前 (Callisto.local) で開けないときは IP アドレスでも試せるよう、開ける URL をすべて出す
    print("iPad のブラウザで次のどれかを開いてください (接続の確認だけなら末尾に /api/version):")
    for name in host_names():
        if name not in ("localhost", "127.0.0.1"):
            print(f"  {scheme}://{name}:{args.port}")
    app.run(host="0.0.0.0", port=args.port, ssl_context=(str(CERT_FILE), str(KEY_FILE)) if secure else None, threaded=True)


if __name__ == "__main__":
    main()
