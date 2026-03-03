"""Integration tests for photo upload endpoint: POST /api/v1/log/<id>/photos."""

from __future__ import annotations

import io
import os

import pytest

from app import create_app
from storage.sqlite import add_log_entry, create_user, get_entry_photo_paths, init_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    _app = create_app()
    _app.config["TESTING"] = True
    # Point UPLOAD_FOLDER at a temp directory so we don't write to the real static dir
    upload_dir = str(tmp_path / "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    _app.config["UPLOAD_FOLDER"] = upload_dir
    return _app


@pytest.fixture
def client(app):
    return app.test_client()


_CSRF_TOKEN = "test-csrf-token"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["csrf_token"] = _CSRF_TOKEN


def _jpeg_bytes(size: int = 100) -> bytes:
    """Return a tiny JPEG-like byte string (valid enough for MIME sniffing in tests)."""
    # Flask test client doesn't validate actual image content; mimetype is passed explicitly.
    return b"\xff\xd8\xff\xe0" + b"\x00" * size


def _upload(client, entry_id, *, field="photo1", content=None, filename="fish.jpg", mimetype="image/jpeg"):
    data = {
        "csrf_token": _CSRF_TOKEN,
        field: (io.BytesIO(content or _jpeg_bytes()), filename, mimetype),
    }
    return client.post(
        f"/api/v1/log/{entry_id}/photos",
        data=data,
        content_type="multipart/form-data",
    )


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

class TestPhotoUploadAuth:
    def test_requires_login(self, client):
        # Provide a CSRF token in the session so the CSRF guard passes;
        # the auth guard (g.user is None) should then return 401.
        with client.session_transaction() as sess:
            sess["csrf_token"] = _CSRF_TOKEN
        resp = _upload(client, 1)
        assert resp.status_code == 401
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

class TestPhotoUploadHappy:
    def test_upload_photo1_returns_201(self, client):
        uid = create_user("uploader1", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Pompano")
        _login(client, uid)

        resp = _upload(client, entry_id)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["ok"] is True
        assert "photo1_path" in body["data"]
        assert body["data"]["entry_id"] == entry_id

    def test_photo_saved_to_db(self, client):
        uid = create_user("uploader2", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Drum")
        _login(client, uid)

        _upload(client, entry_id)
        paths = get_entry_photo_paths(uid, entry_id)
        assert paths is not None
        assert paths[0] is not None  # photo1_path was set
        assert paths[1] is None       # photo2_path still null

    def test_upload_photo2(self, client):
        uid = create_user("uploader3", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Bluefish")
        _login(client, uid)

        resp = _upload(client, entry_id, field="photo2")
        assert resp.status_code == 201
        body = resp.get_json()
        assert "photo2_path" in body["data"]

    def test_upload_both_photos_at_once(self, client):
        uid = create_user("uploader4", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Tautog")
        _login(client, uid)

        data = {
            "csrf_token": _CSRF_TOKEN,
            "photo1": (io.BytesIO(_jpeg_bytes()), "front.jpg", "image/jpeg"),
            "photo2": (io.BytesIO(_jpeg_bytes()), "back.jpg", "image/jpeg"),
        }
        resp = client.post(
            f"/api/v1/log/{entry_id}/photos",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert "photo1_path" in body["data"]
        assert "photo2_path" in body["data"]

    def test_file_actually_written_to_disk(self, client, app):
        uid = create_user("uploader5", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Redfish")
        _login(client, uid)

        _upload(client, entry_id)
        paths = get_entry_photo_paths(uid, entry_id)
        rel = paths[0]
        # rel is "uploads/<uid>/<uuid>.jpg"; strip "uploads/" prefix
        sub = rel[len("uploads/"):]
        abs_path = os.path.join(app.config["UPLOAD_FOLDER"], sub)
        assert os.path.exists(abs_path)

    def test_png_accepted(self, client):
        uid = create_user("uploader6", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Flounder")
        _login(client, uid)

        resp = _upload(client, entry_id, filename="catch.png", mimetype="image/png")
        assert resp.status_code == 201

    def test_webp_accepted(self, client):
        uid = create_user("uploader7", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Sheepshead")
        _login(client, uid)

        resp = _upload(client, entry_id, filename="catch.webp", mimetype="image/webp")
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestPhotoUploadErrors:
    def test_entry_not_found_returns_404(self, client):
        uid = create_user("erruser1", "pw123456")
        _login(client, uid)

        resp = _upload(client, 99999)
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["error"]["code"] == "not_found"

    def test_wrong_user_cannot_upload(self, client):
        uid1 = create_user("owner1", "pw123456")
        uid2 = create_user("intruder1", "pw123456")
        entry_id = add_log_entry(uid1, "loc1", "Snook")
        _login(client, uid2)

        resp = _upload(client, entry_id)
        assert resp.status_code == 404

    def test_invalid_mime_type_returns_400(self, client):
        uid = create_user("erruser2", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Cobia")
        _login(client, uid)

        resp = _upload(client, entry_id, filename="file.gif", mimetype="image/gif")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"]["code"] == "invalid_file_type"

    def test_no_files_provided_returns_400(self, client):
        uid = create_user("erruser3", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Mahi-mahi")
        _login(client, uid)

        resp = client.post(
            f"/api/v1/log/{entry_id}/photos",
            data={"csrf_token": _CSRF_TOKEN},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_file_too_large_returns_413(self, client):
        uid = create_user("erruser4", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Tarpon")
        _login(client, uid)

        # 9 MB — exceeds _MAX_PHOTO_BYTES (8 MB)
        big_content = b"\xff\xd8\xff\xe0" + b"\x00" * (9 * 1024 * 1024)
        resp = _upload(client, entry_id, content=big_content)
        assert resp.status_code == 413
        body = resp.get_json()
        assert body["error"]["code"] == "file_too_large"


# ---------------------------------------------------------------------------
# Delete cascade
# ---------------------------------------------------------------------------

class TestDeleteCascade:
    def test_delete_removes_photo_file(self, client, app):
        uid = create_user("deluser1", "pw123456")
        entry_id = add_log_entry(uid, "loc1", "Kingfish")
        _login(client, uid)

        _upload(client, entry_id)
        paths = get_entry_photo_paths(uid, entry_id)
        rel = paths[0]
        sub = rel[len("uploads/"):]
        abs_path = os.path.join(app.config["UPLOAD_FOLDER"], sub)
        assert os.path.exists(abs_path), "file should exist before delete"

        del_resp = client.delete(f"/api/v1/log/{entry_id}")
        assert del_resp.status_code == 200

        assert not os.path.exists(abs_path), "file should be gone after delete"

    def test_delete_nonexistent_entry_returns_404(self, client):
        uid = create_user("deluser2", "pw123456")
        _login(client, uid)

        resp = client.delete("/api/v1/log/99999")
        assert resp.status_code == 404
