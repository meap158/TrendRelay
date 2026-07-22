from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "apps/desktop/src/main/index.ts").read_text(encoding="utf-8")
PRELOAD = (ROOT / "apps/desktop/src/preload/index.ts").read_text(encoding="utf-8")


def test_desktop_token_stays_encrypted_in_main_process() -> None:
    assert "safeStorage.encryptString(token)" in MAIN
    assert "safeStorage.decryptString(encrypted)" in MAIN
    assert 'join(app.getPath("userData"), "device-session.bin")' in MAIN
    assert "access_token" not in PRELOAD
    assert "Authorization" not in PRELOAD


def test_desktop_ipc_and_navigation_are_origin_scoped() -> None:
    assert "assertTrustedSender(event)" in MAIN
    assert "senderOrigin !== new URL(webOrigin).origin" in MAIN
    assert 'window.webContents.on("will-navigate"' in MAIN
    assert "target.origin !== new URL(apiOrigin).origin" in MAIN
    assert '!input.path.startsWith("/api/")' in MAIN


def test_preload_exposes_only_the_authorized_desktop_capabilities() -> None:
    for capability in ("status", "pair", "signOut", "apiRequest"):
        assert f"{capability}:" in PRELOAD
    assert "contextIsolation: true" in MAIN
    assert "nodeIntegration: false" in MAIN
    assert "sandbox: true" in MAIN
    assert 'preload: join(__dirname, "../preload/index.js")' in MAIN
