from app.services.visual_text_tracks import TextObservation, bbox_iou, build_text_tracks


def obs(index: int, time_ms: int, text: str, bbox=(390, 590, 890, 652), confidence=0.99) -> TextObservation:
    return TextObservation(
        observation_id=f"o{index}",
        time_ms=time_ms,
        text=text,
        confidence=confidence,
        bbox=bbox,
        provider="paddleocr",
        model="PP-OCRv6_small",
        frame_id=f"f{index}",
    )


def test_iou_tracks_stable_geometry() -> None:
    assert bbox_iou((0, 0, 100, 100), (10, 10, 110, 110)) > 0.65
    assert bbox_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_repeated_frame_observations_collapse_into_semantic_tracks() -> None:
    observations = []
    for index in range(11):
        observations.append(obs(index, index * 200, "爸爸,这个人也太帅了吧"))
    for index in range(11, 20):
        observations.append(obs(index, index * 200, "小朋友你也想成为我这么帅的男人吗", bbox=(260, 590, 1020, 652)))

    tracks = build_text_tracks(observations)

    assert len(tracks) == 2
    assert tracks[0].canonical_text == "爸爸,这个人也太帅了吧"
    assert len(tracks[0].observations) == 11
    assert tracks[1].canonical_text == "小朋友你也想成为我这么帅的男人吗"
    assert len(tracks[1].observations) == 9


def test_minor_ocr_variation_can_join_same_track_and_major_change_cannot() -> None:
    tracks = build_text_tracks([
        obs(1, 0, "所有东西", confidence=0.94),
        obs(2, 200, "所有东酉", confidence=0.70),
        obs(3, 400, "完全不同的文字", confidence=0.99),
    ], min_text_similarity=0.70)

    assert len(tracks) == 2
    assert tracks[0].canonical_text == "所有东西"
    assert len(tracks[0].observations) == 2


def test_same_text_far_away_or_after_large_time_gap_is_new_geometry_track() -> None:
    tracks = build_text_tracks([
        obs(1, 0, "管理员", bbox=(20, 440, 140, 495)),
        obs(2, 2000, "管理员", bbox=(20, 440, 140, 495)),
        obs(3, 2200, "管理员", bbox=(900, 100, 1040, 160)),
    ])
    assert len(tracks) == 3


def test_empty_noise_observation_is_not_a_track() -> None:
    tracks = build_text_tracks([obs(1, 0, "   ")])
    assert tracks == []
