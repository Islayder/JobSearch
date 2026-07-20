from __future__ import annotations

from importlib.resources import files


def test_resume_import_templates_are_package_resources() -> None:
    templates = files("radar_vagas.web").joinpath("templates")
    expected = (
        "resume_import_upload.html",
        "resume_import_list.html",
        "resume_import_review.html",
    )

    for name in expected:
        assert templates.joinpath(name).is_file()
