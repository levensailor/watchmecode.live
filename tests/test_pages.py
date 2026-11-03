import pytest


@pytest.mark.parametrize(("path", "heading"), [("/privacy", "Privacy Policy"), ("/terms", "Terms of Service")])
def test_legal_pages_render(client, settings, path, heading):
    response = client.get(path)
    assert response.status_code == 200
    assert heading in response.text
    assert settings.app_name in response.text
    assert "{{" not in response.text


def test_index_links_to_legal_pages_without_metric_tiles(client):
    page = client.get("/").text
    assert 'href="/privacy"' in page
    assert 'href="/terms"' in page
    assert "metric-" not in page
