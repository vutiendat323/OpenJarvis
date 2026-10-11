"""Speech timing reported by the ordering voice benchmark."""

from scripts.benchmark_ordering_voice import speech_metrics


def test_speech_metrics_time_first_speech_and_count_segments_before_first_tool():
    speech = [(0.8, "Dạ, em kiểm tra."), (2.5, "Có hai món.")]

    assert speech_metrics(speech, 1.0) == {
        "time_to_first_speech_s": 0.8,
        "speech_segments": [
            {"at_s": 0.8, "text": "Dạ, em kiểm tra."},
            {"at_s": 2.5, "text": "Có hai món."},
        ],
        "segments_before_first_tool": 1,
    }


def test_speech_metrics_without_speech_or_tools():
    assert speech_metrics([], None) == {
        "time_to_first_speech_s": None,
        "speech_segments": [],
        "segments_before_first_tool": None,
    }
