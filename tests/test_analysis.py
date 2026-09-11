import numpy as np
import pytest

from bcikit.analysis import BAND_DEFINITIONS, analyze_eeg, quality_label


def test_alpha_signal_has_alpha_peak_and_dominant_band():
    sample_rate = 250
    time = np.arange(sample_rate * 4, dtype=np.float64) / sample_rate
    alpha = 20.0 * np.sin(2.0 * np.pi * 10.0 * time)
    samples = np.column_stack([alpha] * 8)

    result = analyze_eeg(samples, sample_rate)

    peak_mask = (result.frequencies_hz >= 1.0) & (result.frequencies_hz <= 45.0)
    peak = result.frequencies_hz[peak_mask][np.argmax(result.average_psd_uv2_per_hz[peak_mask])]
    alpha_index = [name for name, _, _ in BAND_DEFINITIONS].index("Alpha")
    assert peak == pytest.approx(10.0, abs=0.5)
    assert np.argmax(result.average_relative_band_power) == alpha_index
    assert result.channel_count == 8


def test_quality_flags_flatline_and_line_noise_below_clean_signal():
    sample_rate = 250
    time = np.arange(sample_rate * 4, dtype=np.float64) / sample_rate
    clean = 12.0 * np.sin(2.0 * np.pi * 10.0 * time)
    noisy = clean + 30.0 * np.sin(2.0 * np.pi * 50.0 * time)

    clean_result = analyze_eeg(clean[:, None], sample_rate)
    noisy_result = analyze_eeg(noisy[:, None], sample_rate)
    flat_result = analyze_eeg(np.zeros((sample_rate * 4, 1)), sample_rate)

    assert clean_result.quality_scores[0] >= 75.0
    assert noisy_result.quality_scores[0] < clean_result.quality_scores[0]
    assert flat_result.quality_scores[0] < 45.0
    assert quality_label(float(flat_result.quality_scores[0])) == "较差"


def test_analysis_requires_two_seconds_of_finite_samples():
    with pytest.raises(ValueError, match="two seconds"):
        analyze_eeg(np.zeros((499, 8)), 250)
    invalid = np.zeros((500, 8))
    invalid[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        analyze_eeg(invalid, 250)
