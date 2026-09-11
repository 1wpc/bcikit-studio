import numpy as np

from bcikit.filtering import EEGDisplayFilter


def test_display_filter_is_stateful_and_finite():
    sample_rate = 250
    time = np.arange(sample_rate * 2) / sample_rate
    signal = 12 * np.sin(2 * np.pi * 10 * time) + 20 * np.sin(2 * np.pi * 50 * time)
    samples = np.column_stack([signal] * 8)
    eeg_filter = EEGDisplayFilter(sample_rate, 8)
    first = eeg_filter.process(samples[:250])
    second = eeg_filter.process(samples[250:])
    assert first.shape == (250, 8)
    assert second.shape == (250, 8)
    assert np.isfinite(second).all()
    assert np.std(second[:, 0]) < np.std(samples[250:, 0])
