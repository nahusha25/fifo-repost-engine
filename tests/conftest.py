"""Pytest configuration and Hypothesis profile registration."""

import os

from hypothesis import settings

settings.register_profile("ci", max_examples=50, deadline=None)
settings.register_profile("dev", max_examples=300, deadline=None)
settings.register_profile("stress", max_examples=1000, deadline=None)

profile = os.getenv("HYPOTHESIS_PROFILE", "dev").strip()
settings.load_profile(profile)
