"""Test 03 — Files CRUD, path-traversal guard, project create/run/test/export, sessions."""
from __future__ import annotations

import io
import zipfile


def test_files_crud(api):
    content = "سلام دنیا\nsecond line"
    # PUT (create)
    r = api.client.put("/v1/files/content",
                       json={"path": "ut-dir/f1.txt", "content": content},
                       headers=api.headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "path": "ut-dir/f1.txt", "size": len(content)}
    # GET list (dir)
    r = api.client.get("/v1/files", params={"path": "ut-dir"}, headers=api.headers)
    assert r.status_code == 200
    items = {i["name"]: i for i in r.json()["items"]}
    assert "f1.txt" in items
    assert items["f1.txt"]["type"] == "file"
    # size is reported in bytes (UTF-8), not characters
    assert items["f1.txt"]["size"] == len(content.encode("utf-8"))
    # GET content
    r = api.client.get("/v1/files/content", params={"path": "ut-dir/f1.txt"},
                       headers=api.headers)
    assert r.status_code == 200
    assert r.json()["content"] == content
    # DELETE
    r = api.client.delete("/v1/files", params={"path": "ut-dir/f1.txt"},
                          headers=api.headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # gone
    r = api.client.get("/v1/files/content", params={"path": "ut-dir/f1.txt"},
                       headers=api.headers)
    assert r.status_code == 404


def test_files_path_traversal_blocked(api):
    # read outside the workspace
    r = api.client.get("/v1/files/content", params={"path": "../etc/passwd"},
                       headers=api.headers)
    assert r.status_code == 400
    assert "فضای کاری" in r.json()["detail"]
    # list outside the workspace
    r = api.client.get("/v1/files", params={"path": "../"}, headers=api.headers)
    assert r.status_code == 400
    # write outside the workspace
    r = api.client.put("/v1/files/content",
                       json={"path": "../escape.txt", "content": "nope"},
                       headers=api.headers)
    assert r.status_code == 400
    assert not (api.workspace.parent / "escape.txt").exists()


def test_project_create_run_test_export(api):
    # create
    r = api.client.post("/v1/projects", json={"name": "calc-ut", "template": "python"},
                        headers=api.headers)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["project"]["name"] == "calc-ut"
    assert d["project"]["template"] == "python"
    pid = d["project"]["id"]

    # listed
    r = api.client.get("/v1/projects", headers=api.headers)
    assert r.status_code == 200
    assert any(p["id"] == pid and p["name"] == "calc-ut" for p in r.json()["data"])

    # run → main.py prints add(2,3) = 5
    r = api.client.post(f"/v1/projects/{pid}/run", headers=api.headers)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "add(2,3) = 5" in d["output"]
    assert d["exit_code"] == 0

    # test → unittest discover, exit 0
    r = api.client.post(f"/v1/projects/{pid}/test", headers=api.headers)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True, d
    assert d["exit_code"] == 0
    assert "OK" in d["output"]

    # export → v2: two-step download token (the API key never appears in a URL)
    r = api.client.post(f"/v1/projects/{pid}/export-token", headers=api.headers)
    assert r.status_code == 200
    tok = r.json()["token"]
    # a wrong/absent token must be rejected
    r_bad = api.client.get(f"/v1/projects/{pid}/export", params={"download_token": "nope"})
    assert r_bad.status_code == 401
    assert api.key not in str(r_bad.request.url)  # key is never in the URL
    r = api.client.get(f"/v1/projects/{pid}/export", params={"download_token": tok})
    assert r.status_code == 200
    # one-time: replaying the same token fails
    r2 = api.client.get(f"/v1/projects/{pid}/export", params={"download_token": tok})
    assert r2.status_code == 401
    assert r.headers["content-type"].startswith("application/zip")
    assert zipfile.is_zipfile(io.BytesIO(r.content))
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "main.py" in zf.namelist()

    # export with a wrong query key must be rejected
    r = api.client.get(f"/v1/projects/{pid}/export", params={"api_key": "ddk-nope"})
    assert r.status_code == 401


def test_sessions_flow(api):
    # create session
    r = api.client.post("/v1/sessions", json={"title": "نشست آزمون", "mode": "chat"},
                        headers=api.headers)
    assert r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["title"] == "نشست آزمون"

    # chat completion bound to the session
    r = api.client.post("/v1/chat/completions",
                        json={"messages": [{"role": "user", "content": "سلام"}],
                              "session_id": sid, "stream": False},
                        headers=api.headers)
    assert r.status_code == 200

    # messages persisted: user + assistant
    r = api.client.get(f"/v1/sessions/{sid}/messages", headers=api.headers)
    assert r.status_code == 200
    msgs = r.json()["data"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "سلام"
    assert msgs[1]["content"].startswith("سلام")

    # delete session → messages gone (404)
    r = api.client.delete(f"/v1/sessions/{sid}", headers=api.headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    r = api.client.get(f"/v1/sessions/{sid}/messages", headers=api.headers)
    assert r.status_code == 404
