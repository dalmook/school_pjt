from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.routes.api import router
from app.services.neis_client import NeisClient


class DummySchool:
    def __init__(self):
        self.atpt_ofcdc_sc_code = "J10"
        self.sd_schul_code = "7531038"
        self.school_name = "병점중학교"
        self.school_level = "중학교"
        self.org_name = "경기도교육청"
        self.location_summary = "경기도 화성시"
        self.address = "경기도 화성시 병점동"
        self.tel = "031-000-0000"
        self.homepage = "https://example.sch.kr"
        self.coedu = None
        self.fond_date = None


async def _mock_search(self, query: str, force_refresh: bool = False):
    return [DummySchool()]


def _client_and_session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(router)

    def _get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    from app.db import get_db

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), TestingSession


def test_school_search_api_response_structure(monkeypatch):
    monkeypatch.setattr(NeisClient, "search_schools", _mock_search)
    client, _ = _client_and_session()

    resp = client.get("/api/schools/search", params={"q": "병점중"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["school_name"] == "병점중학교"
    assert data[0]["org_name"] == "경기도교육청"
    assert "atpt_ofcdc_sc_code" in data[0]
    assert "sd_schul_code" in data[0]


def test_add_school_to_group_via_api():
    client, _ = _client_and_session()

    region_resp = client.post("/api/regions", json={"region_name": "병점", "region_type": "생활권", "keyword_rules": None})
    assert region_resp.status_code == 200
    region_id = region_resp.json()["region"]["id"]

    add_resp = client.post(
        f"/api/regions/{region_id}/schools",
        json={
            "schools": [
                {
                    "atpt_ofcdc_sc_code": "J10",
                    "sd_schul_code": "7531038",
                    "school_name": "병점중학교",
                    "school_level": "중학교",
                    "address": "경기도 화성시 병점동",
                    "display_order": 0,
                }
            ]
        },
    )
    assert add_resp.status_code == 200
    schools = add_resp.json()["schools"]
    assert len(schools) == 1
    assert schools[0]["school_name"] == "병점중학교"


def test_delete_region_via_api():
    client, _ = _client_and_session()
    region_resp = client.post("/api/regions", json={"region_name": "삭제대상", "region_type": None, "keyword_rules": None})
    region_id = region_resp.json()["region"]["id"]

    delete_resp = client.delete(f"/api/regions/{region_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["ok"] is True

    list_resp = client.get("/api/regions")
    assert all(item["id"] != region_id for item in list_resp.json()["regions"])


def test_delete_school_and_reregister_via_api():
    client, _ = _client_and_session()
    region_resp = client.post("/api/regions", json={"region_name": "병점2", "region_type": "생활권", "keyword_rules": None})
    region_id = region_resp.json()["region"]["id"]

    add_payload = {
        "schools": [
            {
                "atpt_ofcdc_sc_code": "J10",
                "sd_schul_code": "7531038",
                "school_name": "병점중학교",
                "school_level": "중학교",
                "address": "경기도 화성시 병점동",
                "display_order": 0,
            }
        ]
    }
    add_resp = client.post(f"/api/regions/{region_id}/schools", json=add_payload)
    school_id = add_resp.json()["schools"][0]["id"]

    del_resp = client.delete(f"/api/regions/{region_id}/schools/{school_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    detail_resp = client.get(f"/api/regions/{region_id}")
    active_rows = [row for row in detail_resp.json()["schools"] if row["is_active"]]
    assert len(active_rows) == 0

    readd_resp = client.post(f"/api/regions/{region_id}/schools", json=add_payload)
    assert readd_resp.status_code == 200
    assert len(readd_resp.json()["schools"]) == 1
    assert readd_resp.json()["schools"][0]["is_active"] is True
