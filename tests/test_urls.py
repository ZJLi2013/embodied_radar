from radar.urls import normalize


def test_strips_scheme_www_query_fragment_and_trailing_slash():
    assert (
        normalize("https://www.DeepMind.google/blog/gemini-4-argon-for-robotics/?utm_source=x#top")
        == "deepmind.google/blog/gemini-4-argon-for-robotics"
    )


def test_unifies_arxiv_abs_pdf_and_versions():
    expected = "arxiv.org/abs/2610.10498"
    assert normalize("https://arxiv.org/abs/2610.10498v2") == expected
    assert normalize("http://arxiv.org/pdf/2610.10498.pdf") == expected
    assert normalize("arxiv.org/pdf/2610.10498v1") == expected


def test_accepts_bare_alias_from_config():
    assert normalize("geeksongs.github.io/agentic_robotics") == "geeksongs.github.io/agentic_robotics"
