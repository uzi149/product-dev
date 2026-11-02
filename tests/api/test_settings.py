"""The read-only settings for the Settings page (step 5.5): only safe values."""

from shared.config import get_settings


def test_the_main_settings_are_listed_without_secrets(client, monkeypatch):
    monkeypatch.setenv("VLM_BACKEND", "api")
    monkeypatch.setenv("VLM_BASE_URL", "https://user:secret@vlm.example.org:8443/v1")
    monkeypatch.setenv("VLM_API_KEY", "sk-very-secret")
    get_settings.cache_clear()
    try:
        sections = client.get("/settings").json()
    finally:
        get_settings.cache_clear()
    titles = [s["title"] for s in sections]
    assert titles[:3] == ["Vision-language model", "Embeddings (local)", "Analysis"]
    assert "Triggers" in titles and "Retention" in titles
    backend = sections[0]["items"][0]
    assert backend["value"] == "API (vlm.example.org)" and backend["env"] == "VLM_BACKEND"
    text = str(sections)
    assert "secret" not in text and "sk-" not in text and "8443" not in text
    analysis = {i["label"]: i["value"] for i in sections[2]["items"]}
    assert analysis["Window"] == "12 s" and analysis["Keyframes per window"] == "6"
