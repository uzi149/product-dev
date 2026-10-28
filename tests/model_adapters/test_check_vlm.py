"""The setup check command (`python -m shared.model_adapters.check_vlm`), with a fake model."""

import cv2
import numpy as np

from shared.model_adapters import check_vlm
from shared.model_adapters.vlm import VLMRequestRejected
from tests.fakes import FakeVLM


def run(monkeypatch, capsys, vlm, *args):
    built = []

    def fake_build(settings):
        built.append(settings)
        return vlm

    monkeypatch.setattr(check_vlm, "build_vlm", fake_build)
    code = check_vlm.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err, built


def test_it_sends_the_sample_picture_and_prints_the_answer(monkeypatch, capsys):
    vlm = FakeVLM(["A red ball next to a blue box."])
    code, out, _, _ = run(monkeypatch, capsys, vlm)
    assert code == 0
    assert vlm.calls == [
        {"frames": 1, "prompt": check_vlm.PROMPT, "system": None, "max_tokens": 120}
    ]
    assert "A red ball next to a blue box." in out and "fake-vlm" in out


def test_it_sends_the_given_pictures(monkeypatch, capsys, tmp_path):
    paths = []
    for n in range(2):
        path = tmp_path / f"{n}.jpg"
        cv2.imwrite(str(path), np.zeros((50, 80, 3), np.uint8))
        paths.append(str(path))
    vlm = FakeVLM(["ok"])
    assert run(monkeypatch, capsys, vlm, *paths)[0] == 0
    assert vlm.calls[0]["frames"] == 2


def test_an_unreadable_picture_is_an_error(monkeypatch, capsys, tmp_path):
    code, _, err, _ = run(monkeypatch, capsys, FakeVLM(["ok"]), str(tmp_path / "missing.jpg"))
    assert code == 2 and "could not read" in err


def test_a_failing_model_is_reported(monkeypatch, capsys):
    vlm = FakeVLM([VLMRequestRejected("HTTP 401: No auth credentials found")])
    code, _, err, _ = run(monkeypatch, capsys, vlm)
    assert code == 1 and "VLMRequestRejected" in err and "401" in err


def test_backend_flag_overrides_the_settings(monkeypatch, capsys):
    _, out, _, built = run(monkeypatch, capsys, FakeVLM(["ok"]), "--backend", "local")
    assert built[0].vlm_backend == "local"
    assert "backend: local" in out
