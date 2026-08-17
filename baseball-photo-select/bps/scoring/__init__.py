"""Scoring stages (spec 02 §6).

Order matters: cheap, certain rejections first (exposure), then subject
location, then the sharpness measurement that only means anything once we know
*what* to measure. Nothing here deletes or moves files — it only produces
scores and ratings for composite.py to turn into stars.
"""
