from django.test import Client


def test_server_rendered_home() -> None:
    response = Client().get("/")

    assert response.status_code == 200
    assert b"<main>" in response.content
