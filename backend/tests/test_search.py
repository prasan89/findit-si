"""Tests for the cross-video search module."""
import pytest
import pytest_asyncio

from app.search import search_observations, _tokenize, _relevance_score, _group_observations
from tests.fixtures import (
    RECORDING_ID_1, RECORDING_ID_2,
    insert_recording, insert_observation,
)


class TestTokenize:
    def test_removes_stopwords(self):
        tokens = _tokenize("where did I last see my blue notebook")
        assert "blue" in tokens
        assert "notebook" in tokens
        assert "where" not in tokens
        assert "did" not in tokens
        assert "i" not in tokens
        assert "my" not in tokens

    def test_lowercases(self):
        tokens = _tokenize("Blue Notebook")
        assert "blue" in tokens
        assert "notebook" in tokens

    def test_empty_query(self):
        assert _tokenize("") == []

    def test_numbers_kept(self):
        tokens = _tokenize("bag123")
        assert "bag123" in tokens


class TestRelevanceScore:
    def test_exact_label_match(self):
        score = _relevance_score(
            ["blue", "notebook"],
            "blue notebook",
            {},
            None,
        )
        assert score > 0.7

    def test_partial_match(self):
        score = _relevance_score(
            ["blue", "notebook"],
            "notebook",
            {},
            None,
        )
        assert score > 0.3

    def test_no_match(self):
        score = _relevance_score(
            ["blue", "notebook"],
            "red car",
            {},
            None,
        )
        assert score < 0.3

    def test_attribute_match(self):
        score = _relevance_score(
            ["spiral"],
            "notebook",
            {"distinctive_details": "spiral binding"},
            None,
        )
        assert score > 0.3

    def test_empty_terms_returns_one(self):
        score = _relevance_score([], "anything", {}, None)
        assert score == 1.0


class TestGroupObservations:
    def test_groups_nearby(self):
        obs = [
            {"recording_id": "r1", "video_timestamp_seconds": 0.0, "combined_score": 0.8, "observation_id": "o1"},
            {"recording_id": "r1", "video_timestamp_seconds": 3.0, "combined_score": 0.9, "observation_id": "o2"},
            {"recording_id": "r1", "video_timestamp_seconds": 4.0, "combined_score": 0.7, "observation_id": "o3"},
        ]
        grouped = _group_observations(obs)
        # 0.0 and 3.0/4.0 are within 5s — but 3.0 - 0.0 = 3.0 ≤ 5, and 4.0 - 3.0 = 1.0 ≤ 5
        # All three are consecutive and within window — becomes 1 group
        assert len(grouped) == 1
        assert grouped[0]["observation_id"] == "o2"  # highest combined_score

    def test_separates_distant(self):
        obs = [
            {"recording_id": "r1", "video_timestamp_seconds": 0.0, "combined_score": 0.8, "observation_id": "o1"},
            {"recording_id": "r1", "video_timestamp_seconds": 10.0, "combined_score": 0.9, "observation_id": "o2"},
        ]
        grouped = _group_observations(obs)
        assert len(grouped) == 2

    def test_different_recordings_not_grouped(self):
        obs = [
            {"recording_id": "r1", "video_timestamp_seconds": 0.0, "combined_score": 0.8, "observation_id": "o1"},
            {"recording_id": "r2", "video_timestamp_seconds": 1.0, "combined_score": 0.9, "observation_id": "o2"},
        ]
        grouped = _group_observations(obs)
        assert len(grouped) == 2


@pytest.mark.asyncio
class TestSearchObservations:
    async def test_no_recordings_returns_empty(self, db_path):
        result = await search_observations("blue notebook", db_path)
        assert result.best_sighting is None
        assert result.searched_recordings == 0
        assert result.timestamp_ordering_reliable is False

    async def test_no_indexed_recordings(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1, processing_status="PENDING")
        result = await search_observations("blue notebook", db_path)
        assert result.best_sighting is None
        assert result.searched_recordings == 0

    async def test_finds_matching_observation(self, test_db, db_path):
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-01-15T10:00:00+00:00",
        )
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="blue notebook",
            object_attributes={"color": "blue", "shape": "rectangular"},
            recording_datetime="2024-01-15T10:00:00+00:00",
        )
        result = await search_observations("blue notebook", db_path)
        assert result.best_sighting is not None
        assert "notebook" in result.best_sighting.object_label.lower()
        assert result.searched_recordings == 1
        assert result.total_candidates >= 1

    async def test_no_match_returns_empty_sighting(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="red car",
            object_attributes={"color": "red"},
        )
        result = await search_observations("blue notebook", db_path)
        assert result.best_sighting is None
        assert result.searched_recordings == 1

    async def test_cross_video_most_recent_first(self, test_db, db_path):
        """The recording with the later timestamp should come first."""
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-01-10T08:00:00+00:00",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime="2024-06-20T14:00:00+00:00",
        )
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="blue notebook",
            recording_datetime="2024-01-10T08:00:00+00:00",
            video_timestamp_seconds=5.0,
        )
        await insert_observation(
            test_db, RECORDING_ID_2,
            object_label="blue notebook",
            recording_datetime="2024-06-20T14:00:00+00:00",
            video_timestamp_seconds=5.0,
        )
        result = await search_observations("blue notebook", db_path)
        assert result.timestamp_ordering_reliable is True
        assert result.best_sighting.recording_id == RECORDING_ID_2

    async def test_missing_timestamps_degrades_gracefully(self, test_db, db_path):
        """Recording with no datetime still returns results, ordering unreliable."""
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime=None,
        )
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="blue notebook",
            recording_datetime=None,
        )
        result = await search_observations("blue notebook", db_path)
        assert result.best_sighting is not None
        assert result.timestamp_ordering_reliable is False
        assert "No recording timestamps" in result.ordering_explanation

    async def test_mixed_timestamps_partial_reliable(self, test_db, db_path):
        """One recording with timestamp, one without — ordering not fully reliable."""
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-01-10T08:00:00+00:00",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime=None,
        )
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="blue notebook",
            recording_datetime="2024-01-10T08:00:00+00:00",
        )
        await insert_observation(
            test_db, RECORDING_ID_2,
            object_label="blue notebook",
            recording_datetime=None,
        )
        result = await search_observations("blue notebook", db_path)
        assert result.timestamp_ordering_reliable is False
        assert "Some recordings" in result.ordering_explanation

    async def test_similar_objects_ranked_by_score(self, test_db, db_path):
        """Among similar objects, higher detection_score should rank higher (no timestamps)."""
        await insert_recording(test_db, RECORDING_ID_1, recording_datetime=None)
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="notebook",
            detection_score=0.5,
            video_timestamp_seconds=1.0,
            recording_datetime=None,
        )
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="notebook",
            detection_score=0.95,
            video_timestamp_seconds=30.0,
            recording_datetime=None,
        )
        result = await search_observations("notebook", db_path)
        assert result.best_sighting is not None
        assert result.best_sighting.detection_score > 0.5

    async def test_returns_source_frame_path(self, test_db, db_path):
        """Result must carry the source_frame_path so the UI can show evidence."""
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="blue notebook",
            source_frame_path="frames/abc/frame_001.jpg",
        )
        result = await search_observations("blue notebook", db_path)
        assert result.best_sighting is not None
        assert result.best_sighting.source_frame_path == "frames/abc/frame_001.jpg"
