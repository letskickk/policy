from backend.openai_vector_store import reconstruct_winner_identity


def test_reconstruct_identity_extracts_spaced_gov_title_and_name_from_evidence():
    position, name = reconstruct_winner_identity(
        {},
        "민생 제일, 혁신 경기, 잘사는 경제수도 경기도를 만듭니다. 경기도 지사 김동연",
    )
    assert position == "경기도 지사"
    assert name == "김동연"


def test_reconstruct_identity_cleans_name_suffix_from_meta():
    position, name = reconstruct_winner_identity(
        {"position": "경기도지사", "name": "김동연 후보"},
        "",
    )
    assert position == "경기도지사"
    assert name == "김동연"
