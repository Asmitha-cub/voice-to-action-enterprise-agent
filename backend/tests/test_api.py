from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get('/health')
        assert response.status_code == 200
        body = response.json()
        assert body['status'] == 'ok'
        assert 'email_configured' in body


def test_contacts_endpoint_returns_seeded_data():
    with TestClient(app) as client:
        response = client.get('/contacts')
        assert response.status_code == 200
        assert len(response.json()['contacts']) >= 1
